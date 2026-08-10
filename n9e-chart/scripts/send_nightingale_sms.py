#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import logging
import traceback
import requests
from datetime import datetime
from http.client import HTTPConnection
from logging.handlers import TimedRotatingFileHandler

# ⚙️ 开启底层 HTTP 报文调试（仅开发调试使用，生产环境可注释掉）
HTTPConnection.debuglevel = 0  # 关闭 HTTP 原始报文日志（避免噪音）

# 📁 定义日志目录和文件名（从环境变量读取，支持容器化部署）
LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")
log_file = "send_nightingale_sms.log"
os.makedirs(LOG_DIR, exist_ok=True)  # 若目录不存在则自动创建
log_path = os.path.join(LOG_DIR, log_file)

# ⏱ 短信网关请求超时（秒，可通过环境变量调整）
SMS_API_TIMEOUT = int(os.getenv("SMS_API_TIMEOUT", "10"))

# 📝 定义日志格式（包含时间、日志级别、模块名、消息内容）
log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

# 📦 文件日志处理器：每天切割一次，保留 7 天的日志
file_handler = TimedRotatingFileHandler(
    filename=log_path,
    when='midnight',        # 每天午夜切割一次
    interval=1,             # 间隔周期为 1 天
    backupCount=7,          # 最多保留 7 个旧文件
    encoding='utf-8',
    utc=False               # 使用本地时间命名日志文件
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)

# 📺 控制台日志处理器：同时输出到屏幕
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG)

# 🔧 日志主对象配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 📉 降低 requests 和 urllib3 的日志级别，避免太多输出
logging.getLogger("requests").setLevel(logging.WARNING)
urllib3_log = logging.getLogger("urllib3")
urllib3_log.setLevel(logging.DEBUG)
urllib3_log.propagate = True


def load_payload():
    """从标准输入读取 JSON 数据（外部系统传入）"""
    try:
        payload = json.load(sys.stdin)
        logger.debug("✅ 成功读取告警原始数据:\n%s", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception:
        logger.error("❌ 无法解析输入的 JSON 格式，请检查格式是否正确")
        logger.debug(traceback.format_exc())
        sys.exit(1)


def aggregate_events(payload):
    """
    聚合告警事件：根据 title + group + is_recover 分组
    每组包含主机列表、主机名称、事件ID、告警描述、告警级别、时间等
    """
    raw_events = payload.get('events') or [payload.get('event')]
    agg = {}

    for ev in raw_events:
        if not isinstance(ev, dict):
            logger.warning("⚠️ 跳过非字典结构的事件: %r", ev)
            continue

        tags = ev.get('tags_map', {})
        title = payload.get('tpl', {}).get('title') or ev.get('rule_name', '')
        group = tags.get('group') or ev.get('group_name') or ''
        # 如果 group 为空，尝试使用 origin_prometheus
        origin_prometheus = tags.get('origin_prometheus') or ev.get('origin_prometheus', '')
        if not group and origin_prometheus:
            group = origin_prometheus
        if not group:
            group = 'default'
        is_recover = ev.get('is_recovered', False)
        # 将 origin_prometheus 加入聚合判断
        key = (title, group, origin_prometheus, is_recover)

        inst = tags.get('instance')
        host_name = tags.get('name', '')  # 新增：获取主机名称
        rule_note = ev.get('rule_note') or ev.get('annotations', {}).get('description', '')
        # 恢复事件使用 last_eval_time，告警事件使用 trigger_time
        trigger_time = ev.get('trigger_time')
        last_eval_time = ev.get('last_eval_time')
        tm_ts = last_eval_time if is_recover else trigger_time
        time_str = datetime.fromtimestamp(int(tm_ts)).strftime('%Y-%m-%d %H:%M:%S') if tm_ts else 'N/A'
        trigger_time_str = datetime.fromtimestamp(int(trigger_time)).strftime('%Y-%m-%d %H:%M:%S') if trigger_time else 'N/A'
        severity = ev.get('severity')

        # 获取事件ID
        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        if key not in agg:
            agg[key] = {
                'hosts': [],
                'host_names': [],
                'event_ids': [],
                'notes': [],
                'severity': severity,
                'time': time_str,
                'trigger_time': trigger_time_str,
                'is_origin_prometheus': bool(not (tags.get('group') or ev.get('group_name')) and origin_prometheus)
            }

        if inst:
            agg[key]['hosts'].append(inst)
        if host_name:
            agg[key]['host_names'].append(host_name)
        if event_id:
            agg[key]['event_ids'].append(event_id)
        if rule_note:
            agg[key]['notes'].append(rule_note)

    results = []
    for (title, group, origin_prometheus, is_recover), data in agg.items():
        hosts = sorted(set(data['hosts']))
        if not hosts:
            hosts_str = 'N/A'
        elif len(hosts) == 1:
            hosts_str = hosts[0]
        else:
            hosts_str = f"{';'.join(hosts)} 共计 {len(hosts)} 台"

        host_names = sorted(set(data['host_names']))
        if not host_names:
            host_names_str = ''
        else:
            host_names_str = ";".join(host_names)

        event_ids = sorted(set(data['event_ids']))
        event_ids_str = ";".join(event_ids) if event_ids else ""

        notes_set = sorted(set(data['notes']))
        if len(notes_set) == 1:
            notes_str = notes_set[0]
        else:
            notes_str = "; ".join(notes_set)

        results.append({
            'title': title,
            'group': group,
            'is_origin_prometheus': data.get('is_origin_prometheus', False),
            'hosts': hosts_str,
            'host_names': host_names_str,
            'event_ids': event_ids_str,
            'notes': notes_str,
            'severity': data['severity'],
            'is_recover': is_recover,
            'time': data['time'],
            'trigger_time': data['trigger_time']
        })

    logger.debug("✅ 聚合后的事件信息: %s", results)
    return results


def build_sms_content(item):
    """根据是否恢复状态构建短信内容文本"""
    # 如果使用了 origin_prometheus，显示"集群分组"，否则显示"业务分组"
    group_label = "集群分组" if item.get("is_origin_prometheus", False) else "业务分组"
    if item['is_recover']:
        content = (
            f"{group_label}: {item['group']}\n"
            f"告警级别: S{item['severity']} 恢复\n"
            f"告警名称: {item['title']}\n"
            f"主机地址: {item['hosts']}\n"
        )
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            content += f"主机名称: {item['host_names']}\n"
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            content += f"事件ID: {item['event_ids']}\n"
        # 恢复事件：先显示触发时间，再显示恢复时间
        content += (
            f"触发时间: {item['trigger_time']}\n"
            f"恢复时间: {item['time']}\n"
            f"告警描述: {item['notes']}"
        )
    else:
        content = (
            f"{group_label}: {item['group']}\n"
            f"告警级别: S{item['severity']} 告警\n"
            f"告警名称: {item['title']}\n"
            f"主机地址: {item['hosts']}\n"
        )
        # 只有当主机名称不为空时才显示
        if item['host_names']:
            content += f"主机名称: {item['host_names']}\n"
        # 只有当事件ID不为空时才显示
        if item['event_ids']:
            content += f"事件ID: {item['event_ids']}\n"
        content += (
            f"触发时间: {item['time']}\n"
            f"告警描述: {item['notes']}"
        )
    logger.debug("📨 构造的短信内容: \n%s", content)
    return content


def current_timestamp_str():
    """生成当前时间字符串，用于短信时间字段"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.debug("📅 当前时间戳: %s", ts)
    return ts


def send_sms(content, params, sendto):
    """调用短信发送接口"""
    # 从 params 或环境变量读取配置（params 优先级更高）
    url = params.get('url') or os.getenv('SMS_GATEWAY_URL', 'http://otherqxb.iflytek.com/ifly-exchange-other-service/smsQxb/send')
    tid = params.get('tid') or os.getenv('SMS_TEMPLATE_ID', '13770')
    sms_type = params.get('type') or os.getenv('SMS_TYPE', '0')

    if not url.startswith(('http://', 'https://')):
        logger.warning("⚠️ URL 缺少 http/https 前缀，自动补全")
        url = 'http://' + url

    data = {
        'tid': tid,
        'data': json.dumps({
            "Code": content,
            "time": current_timestamp_str()
        }, ensure_ascii=False),
        'telephone': sendto,
        'type': sms_type
    }

    logger.info("🚀 发送短信到 %s，接口地址: %s", sendto, url)
    logger.debug("📤 POST 数据: %s", data)

    try:
        resp = requests.post(url, data=data,
                             headers={'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
                             timeout=SMS_API_TIMEOUT)
        logger.info("✅ 接口响应码: %s", resp.status_code)
        logger.debug("📩 响应内容: %s", resp.text)
    except Exception as e:
        logger.error("❌ 发送请求失败: %s", e)
        logger.debug(traceback.format_exc())


def aggregate_and_send():
    """主流程：加载数据 -> 聚合事件 -> 构建短信 -> 发送"""
    payload = load_payload()
    sms_items = aggregate_events(payload)
    params = payload.get('params', {})
    targets = payload.get('sendtos', [])

    for item in sms_items:
        content = build_sms_content(item)
        for to in targets:
            send_sms(content, params, to)


def main():
    """主函数入口"""
    try:
        aggregate_and_send()
    except Exception as e:
        logger.error("❌ 执行过程中发生未知异常: %s", e)
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
