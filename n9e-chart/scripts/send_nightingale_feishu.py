#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🍃  send_nightingale_feishu.py  (卡片版)
========================================
> Nightingale → 飞书机器人 · **交互式消息卡片**

功能：
- 同类告警聚合
- 主机与描述信息去重
- 飞书推送格式为 `互动卡片`，支持 Markdown + 按钮跳转

使用方式：
    cat payload.json | python3 send_nightingale_feishu.py
"""

import sys
import os
import json
import logging
import traceback
import base64
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from http.client import HTTPConnection
from typing import List, Dict, Any, Optional
import requests

# ============================================================================
# 配置区：从环境变量读取（支持容器化部署和 K8s Secret 注入）
# ============================================================================

# N9E 告警平台地址
DEFAULT_DOMAIN_URL = os.getenv("DEFAULT_DOMAIN_URL", "http://n9e-center.iflytek.com")

# 回调服务地址（告警屏蔽、AI 分析、协同群功能）
CALLBACK_SERVER_URL = os.getenv("CALLBACK_SERVER_URL", "http://n9e-gateway.n9e.svc.cluster.local:5000")

# 飞书开放平台域名
FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "open.xfchat.iflytek.com")

# 超时配置（秒，可通过环境变量调整，用于避免脚本被 n9e 超时 kill）
FEISHU_WEBHOOK_TIMEOUT = int(os.getenv("FEISHU_WEBHOOK_TIMEOUT", "8"))  # 飞书群机器人 webhook 推送
CALLBACK_API_TIMEOUT = int(os.getenv("CALLBACK_API_TIMEOUT", "6"))      # 回调服务注册（AI 分析）

# ============================================================================
# 日志配置
# ============================================================================

HTTPConnection.debuglevel = 0  # 关闭底层 HTTP 调试信息

LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")
LOG_FILE = "send_nightingale_feishu.log"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)

FMT = "%(__asctime)s %(levelname)s %(name)s:%(lineno)d: %(message)s".replace("__", "")
formatter = logging.Formatter(FMT)

file_hd = TimedRotatingFileHandler(LOG_PATH, when="midnight", interval=1, backupCount=7, encoding="utf-8")
file_hd.setFormatter(formatter)
file_hd.setLevel(logging.DEBUG)

console_hd = logging.StreamHandler(sys.stdout)
console_hd.setFormatter(formatter)
console_hd.setLevel(logging.DEBUG)  # 改为 DEBUG，便于排查超时问题

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_hd)
logger.addHandler(console_hd)

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

###############################################################################
# 工具函数
###############################################################################

def load_payload():
    """从标准输入读取 JSON 数据"""
    try:
        payload = json.load(sys.stdin)
        logger.debug("✅ 成功读取告警原始数据:\n%s", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception:
        logger.error("❌ STDIN 解析失败，请确认输入为 JSON 格式")
        logger.debug(traceback.format_exc())
        sys.exit(1)

def aggregate_events(payload):
    """
    聚合告警事件：
    - 按照 (title, group, is_recover) 进行聚合
    - 合并相同主机/主机名称/事件ID/描述/触发值，生成卡片内容
    """
    raw = payload.get("events") or [payload.get("event")]
    agg = {}

    for ev in raw:
        if not isinstance(ev, dict):
            continue

        tags = ev.get("tags_map", {})
        title = payload.get("tpl", {}).get("title") or ev.get("rule_name", "")
        group = tags.get("group") or ev.get("group_name") or ""
        # 如果 group 为空，尝试使用 origin_prometheus
        origin_prometheus = tags.get("origin_prometheus") or ev.get("origin_prometheus", "")
        if not group and origin_prometheus:
            group = origin_prometheus
        if not group:
            group = "default"
        is_recover = ev.get("is_recovered", False)
        # 将 origin_prometheus 加入聚合判断
        key = (title, group, origin_prometheus, is_recover)

        inst = tags.get("instance")
        host_name = tags.get("name", "")  # 新增：获取主机名称
        note = ev.get("rule_note") or ev.get("annotations", {}).get("description", "")
        trig_val = ev.get("trigger_value")
        # 恢复事件使用 last_eval_time，告警事件使用 trigger_time
        trigger_time = ev.get("trigger_time")
        last_eval_time = ev.get("last_eval_time")
        ts = last_eval_time if is_recover else trigger_time
        time_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
        trigger_time_str = datetime.fromtimestamp(int(trigger_time)).strftime("%Y-%m-%d %H:%M:%S") if trigger_time else "N/A"
        sev = ev.get("severity")

        # 提取 event_id，用于构造详情跳转链接
        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        # 初始化每组聚合桶
        bucket = agg.setdefault(key, {
            "hosts": set(), "host_names": set(), "event_ids": set(), "notes": set(), "values": set(),
            "severity": sev, "time": time_str, "trigger_time": trigger_time_str,
            "event_ids": set(),
            "is_origin_prometheus": bool(not (tags.get("group") or ev.get("group_name")) and origin_prometheus),
        })

        if inst:
            bucket["hosts"].add(inst)
        if host_name:
            bucket["host_names"].add(host_name)
        if event_id:
            bucket["event_ids"].add(event_id)
        if note:
            bucket["notes"].add(note)
        if trig_val:
            bucket["values"].add(str(trig_val))
        if event_id:
            bucket["event_ids"].add(event_id)

    # 整理聚合结果为列表
    results = []
    for (title, group, origin_prometheus, is_rec), data in agg.items():
        hosts = sorted(data["hosts"])
        hosts_str = "N/A" if not hosts else (hosts[0] if len(hosts) == 1 else f"{';'.join(hosts)} 共计 {len(hosts)} 台")

        host_names = sorted(data["host_names"])
        host_names_str = "" if not host_names else ";".join(host_names)

        event_ids = sorted(data["event_ids"])
        event_ids_str = ";".join(event_ids) if event_ids else ""

        # 聚合告警标志（多台主机）
        is_aggregated = len(hosts) > 1
        instances_list = list(hosts)

        res = {
            "title": title,
            "group": group,
            "is_origin_prometheus": data.get("is_origin_prometheus", False),
            "hosts": hosts_str,
            "host_names": host_names_str,
            "event_ids": event_ids_str,
            "notes": "; ".join(sorted(data["notes"])) or "无",
            "trigger_value": "; ".join(sorted(data["values"])) or "N/A",
            "severity": data["severity"],
            "is_recover": is_rec,
            "time": data["time"],
            "trigger_time": data["trigger_time"],
            "event_id": next(iter(data["event_ids"]), None),  # 只取一个用于详情链接
            "is_aggregated": is_aggregated,
            "instances": instances_list,
        }
        results.append(res)
    return results

###############################################################################
# 飞书卡片组装
###############################################################################

GREEN = "green"   # 表示恢复
RED = "red"       # 表示告警

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def mk_markdown_body(item: dict) -> str:
    """生成 Markdown 内容块，用于卡片展示"""
    if item["is_recover"]:
        # 如果使用了 origin_prometheus，显示"集群分组"，否则显示"业务分组"
        group_label = "集群分组" if item.get("is_origin_prometheus", False) else "业务分组"
        lines = [
            f"**告警级别:** S{item['severity']} 恢复",
            f"**告警名称:** {item['title']}",
            f"**{group_label}:** {item['group']}",
            f"**主机地址:** {item['hosts']}",
        ]
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            lines.append(f"**主机名称:** {item['host_names']}")
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            lines.append(f"**事件ID:** {item['event_ids']}")
        # 恢复事件：先显示触发时间，再显示恢复时间
        lines.extend([
            f"**触发时间:** {item['trigger_time']}",
            f"**恢复时间:** {item['time']}",
            f"**发送时间:** {now_str()}",
            f"**告警描述:** {item['notes']}",
        ])
    else:
        # 如果使用了 origin_prometheus，显示"集群分组"，否则显示"业务分组"
        group_label = "集群分组" if item.get("is_origin_prometheus", False) else "业务分组"
        lines = [
            f"**告警级别:** S{item['severity']} 告警",
            f"**告警名称:** {item['title']}",
            f"**{group_label}:** {item['group']}",
            f"**主机地址:** {item['hosts']}",
        ]
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            lines.append(f"**主机名称:** {item['host_names']}")
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            lines.append(f"**事件ID:** {item['event_ids']}")
        lines.extend([
            f"**触发时值:** {item['trigger_value']}",
            f"**触发时间:** {item['time']}",
            f"**发送时间:** {now_str()}",
            f"**告警描述:** {item['notes']}",
        ])
    return "\n".join(lines)


def build_card(item: dict, payload: Optional[dict] = None, webhook_tokens: Optional[List[str]] = None) -> dict:
    """生成飞书互动卡片的完整 payload（webhook 模式）

    Args:
        item: 告警聚合后的条目
        payload: 原始 payload（用于取 events 等）
        webhook_tokens: 群机器人 webhook token 列表，随 AI 分析 token 一起持久化，
                        回调服务将用它通过 webhook 推送 AI 分析结果卡片回原群
    """
    status_flag = "﹝恢复﹞" if item["is_recover"] else "﹝告警﹞"
    title_text = f"信息化监控告警 🔥 {status_flag} {item['group']}- {item['title']}"
    template_color = GREEN if item["is_recover"] else RED

    # AI 分析按钮
    action_items = []

    # === AI 分析按钮（URL 跳转模式，兼容群机器人 webhook 卡片）===
    # webhook 卡片不支持 button 带 value 回调，必须使用 URL 跳转。
    if payload:
        try:
            raw_events = payload.get('events') or [payload.get('event')]
            if raw_events and isinstance(raw_events[0], dict):
                first_event = raw_events[0]
                tags_map = first_event.get('tags_map', {})
                trigger_time = first_event.get('trigger_time', 0)
                is_aggregated = item.get("is_aggregated", False)

                ai_data = {
                    'rule_name':    first_event.get('rule_name', ''),
                    'severity':     first_event.get('severity', 0),
                    'group_name':   first_event.get('group_name', ''),
                    'instance':     tags_map.get('instance', ''),
                    'hosts':        item.get('hosts', ''),
                    'host_names':   item.get('host_names', ''),
                    'notes':        item.get('notes', ''),
                    'prom_ql':      first_event.get('prom_ql', ''),
                    'trigger_time': trigger_time,
                    'tags_map':     tags_map,
                    'event_id':     item.get('event_id', ''),
                    'is_aggregated': is_aggregated,
                    'time':         item.get('time', ''),
                    # 推送通道：webhook 模式（回调服务通过 webhook 把结果推回原群）
                    '_push_mode':      'webhook',
                    '_webhook_tokens': webhook_tokens or [],
                }

                try:
                    reg_resp = requests.post(
                        f"{CALLBACK_SERVER_URL}/ai_analysis/register",
                        json=ai_data, timeout=CALLBACK_API_TIMEOUT
                    )
                    if reg_resp.status_code == 200:
                        ai_token = reg_resp.json().get("token", "")
                        if ai_token:
                            # URL 跳转模式：点击后浏览器请求 trigger 端点（返回 204 No Content）
                            trigger_url = f"{CALLBACK_SERVER_URL}/ai_analysis/trigger?token={ai_token}"
                            action_items.append({
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🤖 AI分析"},
                                "url": trigger_url,
                                "type": "default",
                            })
                            logger.info("AI 分析按钮已添加, token=%s, url=%s", ai_token, trigger_url)
                    else:
                        logger.warning("AI 分析参数注册失败, status=%s", reg_resp.status_code)
                except Exception as e:
                    logger.warning("AI 分析按钮构建失败: %s", e)
        except Exception as e:
            logger.error("构建 AI 分析按钮失败: %s", e, exc_info=True)

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": mk_markdown_body(item)}},
        {"tag": "hr"},
    ]
    if action_items:
        elements.append({"tag": "action", "actions": action_items})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title_text[:80]},
            "template": template_color,
        },
        "elements": elements,
    }
    return {"msg_type": "interactive", "card": card}

###############################################################################
# 飞书发送函数（webhook 模式）
###############################################################################

def send_feishu(message: dict, token: str):
    """通过 webhook token 向飞书推送一条卡片消息"""
    if not token:
        logger.warning("⚠️ webhook token 缺失，跳过发送")
        return
    url = f"https://{FEISHU_DOMAIN}/open-apis/bot/v2/hook/{token}"
    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=message, timeout=FEISHU_WEBHOOK_TIMEOUT)
        logger.info("推送: token=%s status=%s", token[-8:], resp.status_code)

        try:
            resp_data = resp.json()
        except Exception:
            logger.warning("⚠️ 响应不是 JSON 格式: %s", resp.text)
            return

        if resp_data.get("code") != 0:
            logger.error("❌ 飞书接口返回错误 code=%s msg=%s", resp_data.get("code"), resp_data.get("msg"))
        else:
            logger.debug("📩 响应内容: %s", resp.text)

    except Exception as e:
        logger.error("发送失败: %s", e)
        logger.debug(traceback.format_exc())

###############################################################################
# 主流程入口
###############################################################################

def apply_params_override(params: dict) -> None:
    """
    从 payload.params 覆盖全局配置变量（如果 params 有配置，优先级最高）
    保持向后兼容：n9e 告警渠道通过 params 下发的配置优先于容器环境变量
    优先级: params > 环境变量 > 默认值
    """
    global CALLBACK_SERVER_URL, FEISHU_DOMAIN, DEFAULT_DOMAIN_URL

    if params.get("callback_server_url"):
        CALLBACK_SERVER_URL = params["callback_server_url"]
    if params.get("feishu_domain"):
        FEISHU_DOMAIN = params["feishu_domain"]
    if params.get("domain_url"):
        DEFAULT_DOMAIN_URL = params["domain_url"]


def run():
    """主执行流程：读取 → 聚合 → 构造 → 发送（webhook 模式）"""
    payload = load_payload()
    items = aggregate_events(payload)

    params = payload.get("params", {})
    
    # 应用 params 覆盖（params > 环境变量 > 默认值）
    apply_params_override(params)
    
    tokens = []
    if params.get("access_token"):
        tokens.append(params["access_token"])
    tokens += params.get("access_tokens", [])
    tokens += payload.get("sendtos", [])
    if not tokens:
        logger.warning("⚠️ 未找到任何 webhook token，终止推送")
        return

    for it in items:
        # 把 webhook tokens 传给 build_card，用于 AI 分析结果异步推送
        msg = build_card(it, payload=payload, webhook_tokens=tokens)
        for tk in tokens:
            send_feishu(msg, tk)

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error("程序异常: %s", e)
        logger.debug(traceback.format_exc())
        sys.exit(1)
