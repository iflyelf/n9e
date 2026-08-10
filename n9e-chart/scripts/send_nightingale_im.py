#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import sys, os, json, requests, logging, re, traceback, time, base64
from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
from logging.handlers import TimedRotatingFileHandler
from typing import List, Dict, Any, Optional
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# 配置区：从环境变量读取（支持容器化部署和 K8s Secret 注入）
# ============================================================================

# 日志和临时文件目录（容器内建议挂载 emptyDir 或 PVC）
LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")
IMG_DIR = os.getenv("IMG_DIR", "/data/n9e/alerts/images")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)

# N9E 告警平台地址
DEFAULT_DOMAIN_URL = os.getenv("DEFAULT_DOMAIN_URL", "http://n9e-center.iflytek.com")

# 回调服务地址（告警屏蔽、AI 分析、协同群功能）
CALLBACK_SERVER_URL = os.getenv("CALLBACK_SERVER_URL", "http://n9e-gateway.n9e.svc.cluster.local:5000")

# Grafana 图表渲染配置
GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "http://ifly.iflytek.com/grafana")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", """")
GRAFANA_DASHBOARD_UID = os.getenv("GRAFANA_DASHBOARD_UID", "adl6lsk")
GRAFANA_DATASOURCE_UID = os.getenv("GRAFANA_DATASOURCE_UID", "cefdos8p4hc74c")
GRAFANA_PANEL_ID = 1
GRAFANA_RENDER_WIDTH = 1000
GRAFANA_RENDER_HEIGHT = 500

# 飞书域名配置
FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "open.xfchat.iflytek.com")

# 超时配置（秒，可通过环境变量调整，用于避免脚本被 n9e 超时 kill）
GRAFANA_RENDER_TIMEOUT = int(os.getenv("GRAFANA_RENDER_TIMEOUT", "15"))  # Grafana 图片渲染
FEISHU_API_TIMEOUT = int(os.getenv("FEISHU_API_TIMEOUT", "6"))           # 飞书 API（token/上传/发消息）
GRAFANA_API_TIMEOUT = int(os.getenv("GRAFANA_API_TIMEOUT", "8"))         # Grafana 仪表盘 CRUD
CALLBACK_API_TIMEOUT = int(os.getenv("CALLBACK_API_TIMEOUT", "6"))       # 回调服务注册（屏蔽/AI分析/协同群）

# 时区配置
CHINA_TZ = timezone(timedelta(hours=8))

# ============================================================================
# 日志配置
# ============================================================================

HTTPConnection.debuglevel = 0

fh = TimedRotatingFileHandler(os.path.join(LOG_DIR, "send_nightingale_im.log"),
                              when='midnight', backupCount=7, encoding='utf-8')
fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt); ch.setLevel(logging.DEBUG)  # 改为 DEBUG，便于排查超时问题
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh); logger.addHandler(ch)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

RETRY_MAX = 2
RETRY_DELAY_MIN = 1
RETRY_DELAY_MAX = 2

# ============================================================================
# params 覆盖机制（优先级: params > 环境变量 > 默认值）
# ============================================================================

def apply_params_override(params: dict) -> None:
    """
    从 payload.params 覆盖全局配置变量（如果 params 有配置，优先级最高）
    保持向后兼容：n9e 告警渠道通过 params 下发的配置优先于容器环境变量
    """
    global GRAFANA_BASE_URL, GRAFANA_TOKEN, GRAFANA_DASHBOARD_UID, GRAFANA_DATASOURCE_UID
    global CALLBACK_SERVER_URL, FEISHU_DOMAIN, DEFAULT_DOMAIN_URL
    
    if params.get("grafana_base_url"):
        GRAFANA_BASE_URL = params["grafana_base_url"]
    if params.get("grafana_token"):
        GRAFANA_TOKEN = params["grafana_token"]
    if params.get("grafana_dashboard_uid"):
        GRAFANA_DASHBOARD_UID = params["grafana_dashboard_uid"]
    if params.get("grafana_datasource_uid"):
        GRAFANA_DATASOURCE_UID = params["grafana_datasource_uid"]
    if params.get("callback_server_url"):
        CALLBACK_SERVER_URL = params["callback_server_url"]
    if params.get("feishu_domain"):
        FEISHU_DOMAIN = params["feishu_domain"]
    # domain_url 在 main() 里单独处理（已有逻辑）

# ============================================================================

def timeformat(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def get_access_token(app_id: str, app_secret: str) -> str:
    url = f"https://{FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal"
    try:
        r = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=FEISHU_API_TIMEOUT)
        data = r.json()
        if r.status_code == 200 and data.get("msg") == "ok":
            return data["tenant_access_token"]
        logger.error("获取 Token 失败：%s", data)
    except Exception:
        logger.error("获取 Token 异常", exc_info=True)
    return ""

def load_payload():
    try:
        payload = json.load(sys.stdin)
        logger.debug("成功读取告警原始数据: %s", json.dumps(payload, ensure_ascii=False, indent=2)[:3000])
        return payload
    except Exception:
        logger.error("STDIN 解析失败，请确认输入为 JSON 格式")
        logger.debug(traceback.format_exc())
        sys.exit(1)

def extract_promql_from_event(ev: Dict[str, Any]) -> str:
    prom_ql_raw = ev.get('prom_ql', '')
    if not prom_ql_raw:
        logger.warning("事件中无 prom_ql 字段")
        return ""

    # 提取 instance
    tags = ev.get('tags_map', {})
    instance = tags.get('instance', '')

    # 去除末尾阈值比较（如 > 3, > 85 等），保留 on(...) 子句
    expr = prom_ql_raw.strip()

    # 按 ' and ' 分割，对每个部分处理阈值判断
    parts = expr.split(' and ')
    modified_parts = []

    for part in parts:
        part = part.strip()
        # 检查 part 是否包含函数调用，如果是，检查并移除末尾的阈值部分
        # 例如 "round(...) > 3" -> "round(...)"，但保留 "rate(...) > 0"
        # 实际上我们想移除的是 "round(...) > 3" 中的 > 3，但保留 "rate(...) > 0" 中的 > 0

        # 让我们重新分析需求
        # 从样例看，我们想要移除的是主要表达式后面的纯阈值判断
        # 而不是函数内部的阈值判断

        # 正确的做法是移除表达式末尾的比较操作符
        # 如 round(...) > 3 -> round(...), 但保留 rate(...) > 0
        # 实际上，我们可能想移除所有主要表达式的阈值
        # round(rate(...)) > 3 and on(...) -> round(rate(...)) and on(...)

        # 尝试更精确的逻辑：只移除简单阈值比较
        # 如果 part 包含表达式并以 > N, < N, >= N, <= N, != N, == N 结尾，则移除末尾部分
        # 例如: "round(...) > 3"  -> "round(...)"
        #      "rate(...) > 0"  -> "rate(...)" (如果这是阈值的话)

        # 然而实际上，根据promql样例，我们可能需要移除所有末尾的阈值比较
        # round(...) > 3 -> round(...)
        modified_part = re.sub(r'\s*[<>!=]=?\s*\d+\.?\d*\s*$', '', part).rstrip(' ,')
        if modified_part and modified_part != part:
            # 匹配到了阈值并成功移除
            modified_parts.append(modified_part)
        else:
            # 没有匹配阈值或移除后为空，保留原样
            modified_parts.append(part)

    expr = ' and '.join(modified_parts)

    logger.debug("处理前: %s", prom_ql_raw)
    logger.debug("处理后(无阈值): %s", expr)

    # 添加 instance 标签选择到表达式中
    if instance:
        # 查找所有标签选择器 {...} 并添加 instance
        def add_instance(match):
            inner = match.group(1)
            if 'instance=' not in inner and 'instance!=' not in inner:
                if inner:
                    inner += f',instance="{instance}"'
                else:
                    inner = f'instance="{instance}"'
            return '{' + inner + '}'

        # 匹配 {label=value,...} 这样的标签选择器，并替换所有匹配的
        expr = re.sub(r'\{([^}]*)\}', add_instance, expr)
        logger.debug("添加 instance 后: %s", expr)

    logger.info("最终 PromQL: %s", expr)
    return expr

def extract_alert_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get('events') or [payload.get('event')]
    ev = raw[0] if raw else {}
    if not isinstance(ev, dict):
        return {}
    rule_name = ev.get('rule_name', '')
    tags = ev.get('tags_map', {})
    instance = tags.get('instance', '')
    prom_ql = extract_promql_from_event(ev)
    logger.info("提取告警信息 - 标题: %s, 实例: %s, PromQL: %s", rule_name, instance, prom_ql)
    return {"rule_name": rule_name, "instance": instance, "prom_ql": prom_ql}

def get_grafana_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {GRAFANA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def get_dashboard_config() -> Optional[Dict[str, Any]]:
    url = f"{GRAFANA_BASE_URL}/api/dashboards/uid/{GRAFANA_DASHBOARD_UID}"
    logger.info("获取 Dashboard 配置, URL: %s", url)

    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=get_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
            logger.debug("Dashboard 响应状态: %s", r.status_code)
            if r.status_code == 200:
                data = r.json()
                dashboard = data.get("dashboard", {})
                logger.info("获取 Dashboard 成功, 标题: %s", dashboard.get("title", ""))
                return dashboard
            logger.error("获取 Dashboard 失败: status=%s, body=%s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("获取 Dashboard 异常 (第%d次尝试): %s", attempt + 1, e)

        if attempt < RETRY_MAX - 1:
            delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
            time.sleep(delay)

    logger.error("获取 Dashboard 超过最大重试次数")
    return None

def update_dashboard(dashboard: Dict[str, Any]) -> bool:
    payload = {"dashboard": dashboard, "overwrite": True}
    url = f"{GRAFANA_BASE_URL}/api/dashboards/db"
    logger.info("更新 Dashboard, URL: %s", url)

    for attempt in range(RETRY_MAX):
        try:
            r = requests.post(url, headers=get_grafana_headers(), json=payload, timeout=GRAFANA_API_TIMEOUT, verify=False)
            logger.debug("Dashboard 更新响应: status=%s, body=%s", r.status_code, r.text[:200])
            if r.status_code == 200:
                resp_data = r.json()
                logger.info("Dashboard 更新成功: %s", resp_data.get("status", ""))
                return True
            logger.error("Dashboard 更新失败: status=%s", r.status_code)
        except Exception as e:
            logger.error("Dashboard 更新异常 (第%d次尝试): %s", attempt + 1, e)

        if attempt < RETRY_MAX - 1:
            delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
            time.sleep(delay)

    logger.error("更新 Dashboard 超过最大重试次数")
    return False

def render_panel_image(prom_ql: str, rule_name: str) -> Optional[str]:
    now_utc = datetime.now(timezone.utc)
    from_utc = now_utc - timedelta(hours=6)

    from_str = from_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    temp_dashboard_uid = f"temp_alert_{int(time.time() * 1000)}"

    temp_dashboard = {
        "dashboard": {
            "uid": temp_dashboard_uid,
            "title": rule_name,
            "timezone": "Asia/Shanghai",
            "style": "dark",
            "panels": [
                {
                    "id": 1,
                    "title": rule_name,
                    "type": "timeseries",
                    "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
                    "datasource": {
                        "type": "prometheus",
                        "uid": GRAFANA_DATASOURCE_UID
                    },
                    "targets": [
                        {
                            "datasource": {
                                "type": "prometheus",
                                "uid": GRAFANA_DATASOURCE_UID
                            },
                            "expr": prom_ql,
                            "refId": "A",
                            "editorMode": "code",
                            "range": True,
                            "instant": False,
                            "legendFormat": "__auto"
                        }
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "palette-classic"},
                            "custom": {
                                "lineWidth": 3,
                                "fillOpacity": 10,
                                "drawStyle": "line",
                                "lineInterpolation": "smooth",
                                "showPoints": "auto",
                                "gradientMode": "opacity"
                            }
                        },
                        "overrides": []
                    },
                    "transparent": False,
                    "options": {
                        "legend": {
                            "displayMode": "list",
                            "placement": "bottom",
                            "showLegend": True
                        }
                    }
                }
            ],
            "time": {"from": from_str, "to": to_str},
            "timepicker": {}
        },
        "message": "临时告警图表",
        "overwrite": True
    }

    logger.info("创建临时 Dashboard: %s", temp_dashboard_uid)
    create_url = f"{GRAFANA_BASE_URL}/api/dashboards/db"

    for attempt in range(RETRY_MAX):
        try:
            r = requests.post(create_url, headers=get_grafana_headers(), json=temp_dashboard, timeout=GRAFANA_API_TIMEOUT, verify=False)
            if r.status_code == 200:
                resp_data = r.json()
                logger.info("临时 Dashboard 创建成功: %s", json.dumps(resp_data, ensure_ascii=False)[:200])
                break
            logger.warning("临时 Dashboard 创建失败 (第%d次): status=%s, body=%s", attempt + 1, r.status_code, r.text[:200])
        except Exception as e:
            logger.error("临时 Dashboard 创建异常 (第%d次): %s", attempt + 1, e)

        if attempt < RETRY_MAX - 1:
            time.sleep(0.5)
    else:
        logger.error("临时 Dashboard 创建失败，跳过图表")
        return None

    # 等待 Grafana 处理 Dashboard
    time.sleep(0.5)

    url = (
        f"{GRAFANA_BASE_URL}/render/d-solo/{temp_dashboard_uid}"
        f"?orgId=1&from={from_str}&to={to_str}&timezone=Asia/Shanghai"
        f"&panelId=1&__feature.dashboardSceneSolo=true"
        f"&width={GRAFANA_RENDER_WIDTH}&height={GRAFANA_RENDER_HEIGHT}&scale=1&tz=Asia/Shanghai"
        f"&theme=dark"
    )

    headers = {
        "Authorization": f"Bearer {GRAFANA_TOKEN}",
        "Accept": "image/png,application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Referer": f"{GRAFANA_BASE_URL}/d/{temp_dashboard_uid}?orgId=1&from=now-6h&to=now&timezone=Asia/Shanghai",
        "x-grafana-org-id": "1",
        "x-dashboard-uid": temp_dashboard_uid,
        "x-panel-id": "1",
    }

    logger.info("图片渲染URL: %s", url)

    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=headers, timeout=GRAFANA_RENDER_TIMEOUT, verify=False)
            logger.debug("图片渲染响应: status=%s, Content-Type=%s, Content-Length=%s",
                          r.status_code, r.headers.get("Content-Type", ""), r.headers.get("Content-Length", ""))
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                if content_type.startswith("image"):
                    ts = int(time.time())
                    file_path = os.path.join(IMG_DIR, f"grafana_alert_{ts}.png")
                    with open(file_path, "wb") as f:
                        f.write(r.content)
                    logger.info("✅ Grafana 图片渲染成功: %s (大小: %d bytes)", file_path, len(r.content))

                    delete_temp_dashboard(temp_dashboard_uid)
                    return file_path
                logger.warning("渲染响应非图片: Content-Type=%s, 前200字节: %s",
                               content_type, r.text[:200])
            logger.error("图片渲染失败: status=%s, Content-Type=%s",
                          r.status_code, r.headers.get("Content-Type", ""))
        except requests.Timeout:
            logger.warning("⏱ Grafana 渲染超时 (%ds)，尝试 %d/%d", GRAFANA_RENDER_TIMEOUT, attempt + 1, RETRY_MAX)
        except Exception as e:
            logger.error("❌ 图片渲染异常 (第%d次): %s", attempt + 1, e, exc_info=True)

        if attempt < RETRY_MAX - 1:
            delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
            time.sleep(delay)

    logger.error("图片渲染超过最大重试次数")
    delete_temp_dashboard(temp_dashboard_uid)
    return None


def delete_temp_dashboard(uid: str):
    try:
        r = requests.delete(f"{GRAFANA_BASE_URL}/api/dashboards/uid/{uid}", headers=get_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
        logger.info("删除临时 Dashboard %s: status=%s", uid, r.status_code)
    except Exception as e:
        logger.warning("删除临时 Dashboard %s 失败: %s", uid, e)

def upload_image_to_feishu(token: str, image_path: str) -> str:
    url = f"https://{FEISHU_DOMAIN}/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(RETRY_MAX):
        try:
            with open(image_path, "rb") as f:
                files = {"image": ("alert_chart.png", f, "image/png")}
                data = {"image_type": "message"}
                r = requests.post(url, headers=headers, files=files, data=data, timeout=GRAFANA_RENDER_TIMEOUT)
            resp_data = r.json()
            if r.status_code == 200 and resp_data.get("code") == 0:
                image_key = resp_data.get("data", {}).get("image_key", "")
                logger.info("图片上传飞书成功, image_key: %s", image_key)
                return image_key
            logger.error("图片上传飞书失败 (第%d次尝试): %s", attempt + 1, json.dumps(resp_data, ensure_ascii=False)[:200])
        except Exception as e:
            logger.error("图片上传飞书异常 (第%d次尝试): %s", attempt + 1, e, exc_info=True)

        if attempt < RETRY_MAX - 1:
            delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
            time.sleep(delay)

    logger.error("图片上传飞书超过最大重试次数")
    return ""

def process_grafana_chart(alert_info: Dict[str, Any]) -> str:
    prom_ql = alert_info.get("prom_ql", "")
    rule_name = alert_info.get("rule_name", "")
    logger.info("process_grafana_chart 开始, rule_name=%s, prom_ql=%s", rule_name, prom_ql[:100])

    if not prom_ql:
        logger.warning("无 PromQL，跳过图表生成")
        return ""

    image_path = render_panel_image(prom_ql, rule_name)
    if not image_path:
        logger.error("图片渲染失败")
        return ""

    logger.info("图表处理完成, 图片路径: %s", image_path)
    return image_path

def aggregate_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get('events') or [payload.get('event')]
    agg = {}
    for ev in raw:
        if not isinstance(ev, dict):
            continue

        tags = ev.get('tags_map', {})
        title = payload.get('tpl', {}).get('title') or ev.get('rule_name', '')
        group = tags.get('group') or ev.get('group_name', '')
        origin_prometheus = tags.get('origin_prometheus') or ev.get('origin_prometheus', '')
        if not group and origin_prometheus:
            group = origin_prometheus
        is_recover = ev.get('is_recovered', False)
        key = (title, group, origin_prometheus, is_recover)

        inst = tags.get('instance', '')
        host_name = tags.get('name', '')
        note = ev.get('rule_note') or ev.get('annotations', {}).get('description', '')
        trigger_time = ev.get('trigger_time', 0)
        last_eval_time = ev.get('last_eval_time', 0)
        ts = last_eval_time if is_recover else trigger_time
        tstr = timeformat(int(ts)) if ts else ''
        trigger_tstr = timeformat(int(trigger_time)) if trigger_time else ''
        sev = ev.get('severity', 0)

        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        agg.setdefault(key, {
            "hosts": [], "host_names": [], "event_ids": [], "notes": [], "title": title,
            "group": group,
            "is_origin_prometheus": bool(not (tags.get('group') or ev.get('group_name', '')) and origin_prometheus),
            "time": tstr, "trigger_time": trigger_tstr, "severity": sev, "is_recover": is_recover,
            "event_id": event_id
        })
        if inst:
            agg[key]["hosts"].append(inst)
        if host_name:
            agg[key]["host_names"].append(host_name)
        if event_id:
            agg[key]["event_ids"].append(event_id)
        if note:
            agg[key]["notes"].append(note)

    results = []
    for v in agg.values():
        hosts = sorted(set(v["hosts"]))
        host_names = sorted(set(v["host_names"]))
        event_ids = sorted(set(v["event_ids"]))
        notes = sorted(set(v["notes"]))

        hosts_str = "N/A" if not hosts else (hosts[0] if len(hosts) == 1 else f"{';'.join(hosts)} 共计 {len(hosts)} 台")
        host_names_str = "" if not host_names else ";".join(host_names)
        event_ids_str = ";".join(event_ids) if event_ids else ""

        # 判断是否是聚合告警
        is_aggregated = len(hosts) > 1

        results.append({
            "title": v["title"], "group": v["group"],
            "is_origin_prometheus": v.get("is_origin_prometheus", False),
            "hosts": hosts_str, "host_names": host_names_str,
            "event_ids": event_ids_str,
            "notes": notes[0] if len(notes) == 1 else "; ".join(notes),
            "time": v["time"], "trigger_time": v["trigger_time"], "severity": v["severity"],
            "is_recover": v["is_recover"], "event_id": v.get("event_id"),
            "is_aggregated": is_aggregated  # 新增字段
        })
    return results

def build_markdown(item: Dict[str, Any]) -> str:
    is_recover = item["is_recover"]
    lines = []
    lines.append(f"**告警级别:** S{item['severity']} {'恢复' if is_recover else '告警'}  ")
    if item["group"]:
        group_label = "集群分组" if item.get("is_origin_prometheus", False) else "业务分组"
        lines.append(f"**{group_label}:** {item['group']}  ")
    if item["hosts"]:
        lines.append(f"**主机地址:** {item['hosts']}  ")
    if item["host_names"]:
        lines.append(f"**主机名称:** {item['host_names']}  ")
    if item["event_ids"]:
        lines.append(f"**事件ID:** {item['event_ids']}  ")
    if is_recover:
        # 恢复事件：先显示触发时间，再显示恢复时间
        if item.get("trigger_time"):
            lines.append(f"**触发时间:** {item['trigger_time']}  ")
        if item["time"]:
            lines.append(f"**恢复时间:** {item['time']}  ")
    else:
        # 告警事件：只显示触发时间
        if item["time"]:
            lines.append(f"**触发时间:** {item['time']}  ")
    lines.append(f"**发送时间:** {timeformat(int(datetime.now().timestamp()))}  ")
    if item["notes"]:
        lines.append(f"**告警描述:** {item['notes']}  ")
    return "\n".join(lines)

def extract_emails_from_sendtos(sendtos: List[str]) -> List[str]:
    """从 sendtos 中提取有效邮箱地址
    
    参数:
        sendtos: 通知目标列表，可能包含邮箱、open_id、chat_id 等
    
    返回:
        有效邮箱列表（去重）
    """
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    emails = []
    
    for to in sendtos or []:
        to = (to or "").strip()
        # 只提取符合邮箱格式的条目
        if '@' in to and email_pattern.match(to):
            emails.append(to)
    
    # 去重并保持顺序
    seen = set()
    unique_emails = []
    for email in emails:
        if email not in seen:
            seen.add(email)
            unique_emails.append(email)
    
    logger.info("从 sendtos 中提取到 %d 个有效邮箱: %s", len(unique_emails), unique_emails)
    return unique_emails

def build_mute_option_value(duration: str, group_id: int, group_name: str, rule_name: str,
                             instance: str, trigger_time: int, tags: List[str],
                             is_aggregated: bool = False, instances: List[str] = None) -> str:
    """构建屏蔽选项 value。

    为避免飞书 select_static option value 长度限制导致 200340 错误，
    将完整屏蔽参数先注册到回调服务，option value 只存短 token。
    用户选择后，飞书回调携带 token，回调服务通过 token 取回完整参数执行屏蔽。

    如果注册失败，则回退为 base64 编码方式（可能因长度被飞书拒绝）。
    """
    try:
        mute_data = {
            'duration': duration,
            'group_id': group_id,
            'group_name': group_name,
            'rule_name': rule_name,
            'instance': instance,
            'trigger_time': trigger_time,
            'tags': tags,
            'is_aggregated': is_aggregated
        }
        if is_aggregated and instances:
            mute_data['instances'] = instances

        # 尝试向回调服务注册，获取短 token
        try:
            reg_resp = requests.post(
                f"{CALLBACK_SERVER_URL}/mute/register",
                json=mute_data,
                timeout=CALLBACK_API_TIMEOUT
            )
            if reg_resp.status_code == 200:
                reg_data = reg_resp.json()
                token = reg_data.get("token", "")
                if token:
                    logger.debug("屏蔽参数注册成功, token=%s, duration=%s", token, duration)
                    return token
        except Exception as e:
            logger.warning("屏蔽参数注册失败，回退 base64: %s", e)

        # 回退：直接 base64 编码（可能因长度被飞书拒绝）
        value_json = json.dumps(mute_data, ensure_ascii=False)
        value_encoded = base64.b64encode(value_json.encode('utf-8')).decode('utf-8')
        return value_encoded
    except Exception as e:
        logger.error(f"构建屏蔽选项 value 失败: {e}", exc_info=True)
        return ""

def send_cards(app_id: str, app_secret: str, sendtos: List[str],
               items: List[Dict[str, Any]], domain_url: str, payload: Dict[str, Any] = None):
    logger.info("send_cards 开始, sendtos=%s, items数量=%d", sendtos, len(items))
    token = get_access_token(app_id, app_secret)
    if not token:
        logger.error("获取飞书 Token 失败，无法发送卡片")
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    image_key = ""
    image_path = ""

    # 检查是否是聚合告警（多个主机）
    is_any_aggregated = any(item.get("is_aggregated", False) for item in items)
    if not is_any_aggregated:
        alert_info = extract_alert_info(payload) if payload else {}
        if alert_info.get("prom_ql"):
            logger.info("开始 Grafana 图表流程...")
            try:
                image_path = process_grafana_chart(alert_info)
                if image_path:
                    image_key = upload_image_to_feishu(token, image_path)
                    logger.info("图片上传结果, image_key: %s", image_key)
            except Exception as e:
                logger.error("图表流程异常: %s", e, exc_info=True)

    for item in items:
        is_recover = item["is_recover"]
        status_flag = "﹝恢复﹞" if is_recover else "﹝告警﹞"
        header_text = f"信息化监控告警 🔥 {status_flag} {item['group']}- {item['title']}"
        tpl = "green" if is_recover else "red"
        md = build_markdown(item)

        event_id = item.get("event_id")
        if not event_id:
            notes = item.get("notes", "")
            match = re.search(r'alert-his-events/([^\s;]+)', notes)
            if match:
                event_id = match.group(1)

        elements = []
        if image_key and not item.get("is_aggregated", False):
            elements.append({"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "监控图表"}})
        elements.append({"tag": "markdown", "content": md})

        # 告警屏蔽下拉框 + AI分析按钮（合并到同一个 action 容器，同一行显示）
        action_items = []

        # 告警屏蔽下拉框（select_static + 卡片回调）
        if payload:
            try:
                raw_events = payload.get('events') or [payload.get('event')]
                if raw_events and isinstance(raw_events[0], dict):
                    first_event = raw_events[0]
                    group_id = first_event.get('group_id', 0)
                    group_name = first_event.get('group_name', '')
                    rule_name_for_mute = first_event.get('rule_name', '')
                    trigger_time = first_event.get('trigger_time', 0)
                    tags = first_event.get('tags', [])
                    tags_map = first_event.get('tags_map', {})
                    instance = tags_map.get('instance', 'N/A')

                    is_aggregated = item.get("is_aggregated", False)
                    instances = []
                    if is_aggregated:
                        for ev in raw_events:
                            if isinstance(ev, dict):
                                ev_tags_map = ev.get('tags_map', {})
                                ev_instance = ev_tags_map.get('instance', '')
                                if ev_instance and ev_instance not in instances:
                                    instances.append(ev_instance)

                    durations = [
                        ("屏蔽 1 天", "1d"),
                        ("屏蔽 3 天", "3d"),
                        ("屏蔽 6 天", "6d"),
                        ("屏蔽 1 周", "1w"),
                        ("屏蔽 3 周", "3w"),
                        ("屏蔽 1 个月", "1m"),
                        ("永久屏蔽", "forever"),
                        ("取消屏蔽", "unmute"),
                    ]

                    mute_options = []
                    for label, duration in durations:
                        option_value = build_mute_option_value(
                            duration, group_id, group_name, rule_name_for_mute,
                            instance, trigger_time, tags, is_aggregated, instances
                        )
                        if option_value:
                            mute_options.append({
                                "text": {
                                    "tag": "plain_text",
                                    "content": label
                                },
                                "value": option_value
                            })

                    if mute_options:
                        # select_static 在飞书 v1 卡片中 value 字段为业务参数（dict），
                        # 用户选择某个 option 后，飞书回调会同时携带此 value 和所选 option 的 value
                        # 经验上：缺失 value 字段在部分客户端版本会触发卡片渲染失败（200340）
                        action_items.append({
                            "tag": "select_static",
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "告警屏蔽"
                            },
                            "value": {
                                "action": "mute_alert"
                            },
                            "options": mute_options
                        })
                        logger.info("已构建屏蔽下拉选项，共 %d 个选项", len(mute_options))
            except Exception as e:
                logger.error("构建屏蔽下拉选项失败: %s", e, exc_info=True)

        # ========== 新增：协同群下拉框（创建/解散） ==========
        if payload and sendtos:
            try:
                # 从 sendtos 中提取邮箱
                emails = extract_emails_from_sendtos(sendtos)
                
                if emails:  # 只有存在邮箱时才显示协同群下拉
                    raw_events = payload.get('events') or [payload.get('event')]
                    if raw_events and isinstance(raw_events[0], dict):
                        first_event = raw_events[0]
                        
                        # 构建拉群参数
                        gc_payload = {
                            'emails': emails,
                            'rule_name':  first_event.get('rule_name', ''),
                            'group_name': first_event.get('group_name', ''),
                            'severity':   first_event.get('severity', 0),
                            'hosts':      item.get('hosts', ''),
                            'host_names': item.get('host_names', ''),
                            'notes':      item.get('notes', ''),
                            'time':       item.get('time', ''),
                            'event_id':   item.get('event_id', ''),
                            'is_aggregated': item.get('is_aggregated', False),
                            'is_recover': item.get('is_recover', False),
                            # 飞书凭证（用于回调服务创建群和发送消息）
                            '_feishu_app_id':     app_id,
                            '_feishu_app_secret': app_secret,
                            # 告警卡片快照（用于发送到新群）
                            '_alert_card_snapshot': {
                                'header_text': header_text,
                                'template':    tpl,
                                'markdown':    md,
                                'image_key':   image_key if image_key and not item.get("is_aggregated", False) else "",
                                'event_id':    event_id,
                            },
                        }
                        
                        # 注册拉群参数，获取短 token
                        reg_resp = requests.post(
                            f"{CALLBACK_SERVER_URL}/group_chat/register",
                            json=gc_payload,
                            timeout=CALLBACK_API_TIMEOUT
                        )
                        
                        if reg_resp.status_code == 200:
                            gc_token = reg_resp.json().get("token", "")
                            if gc_token:
                                action_items.append({
                                    "tag": "select_static",
                                    "placeholder": {"tag": "plain_text", "content": "协同群"},
                                    "value": {"action": "group_chat"},
                                    "options": [
                                        {"text": {"tag": "plain_text", "content": "创建协同群"},
                                         "value": f"create_{gc_token}"},
                                        {"text": {"tag": "plain_text", "content": "解散协同群"},
                                         "value": f"dismiss_{gc_token}"},
                                    ]
                                })
                                logger.info("协同群下拉已添加, token=%s, 邮箱数=%d", gc_token, len(emails))
                        else:
                            logger.warning("协同群参数注册失败, status=%s", reg_resp.status_code)
            except Exception as e:
                logger.warning("协同群下拉构建失败: %s", e)
        # ========== 协同群下拉结束 ==========

        # ========== 新增：AI 分析按钮（与告警屏蔽下拉框同行） ==========
        if payload:
            try:
                raw_events = payload.get('events') or [payload.get('event')]
                if raw_events and isinstance(raw_events[0], dict):
                    first_event = raw_events[0]
                    ai_data = {
                        'rule_name':    first_event.get('rule_name', ''),
                        'severity':     first_event.get('severity', 0),
                        'group_name':   first_event.get('group_name', ''),
                        'instance':     first_event.get('tags_map', {}).get('instance', ''),
                        'hosts':        item.get('hosts', ''),
                        'host_names':   item.get('host_names', ''),
                        'notes':        item.get('notes', ''),
                        'prom_ql':      extract_promql_from_event(first_event),
                        'trigger_time': first_event.get('trigger_time', 0),
                        'tags_map':     first_event.get('tags_map', {}),
                        'event_id':     item.get('event_id', ''),
                        'is_aggregated': item.get('is_aggregated', False),
                        'time':         item.get('time', ''),
                        # 方案 A：飞书凭证随 token 持久化，供回调端异步推送 AI 分析卡片
                        '_feishu_app_id':     app_id,
                        '_feishu_app_secret': app_secret,
                    }

                    reg_resp = requests.post(
                        f"{CALLBACK_SERVER_URL}/ai_analysis/register",
                        json=ai_data, timeout=CALLBACK_API_TIMEOUT
                    )
                    if reg_resp.status_code == 200:
                        ai_token = reg_resp.json().get("token", "")
                        if ai_token:
                            action_items.append({
                                "tag": "button",
                                "text": {"content": "🤖 AI分析", "tag": "plain_text"},
                                "type": "default",
                                "value": {"action": "ai_analysis", "token": ai_token}
                            })
                            logger.info("AI 分析按钮已添加, token=%s", ai_token)
                    else:
                        logger.warning("AI 分析参数注册失败, status=%s", reg_resp.status_code)
            except Exception as e:
                logger.warning("AI 分析按钮构建失败: %s", e)
        # ========== AI 分析按钮结束 ==========

        # 告警屏蔽下拉框 + 协同群下拉 + AI分析按钮 放在同一个 action 容器（同一行显示）
        if action_items:
            elements.append({
                "tag": "action",
                "actions": action_items
            })

        content = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": header_text}, "template": tpl},
            "elements": elements
        }
        body = {"msg_type": "interactive", "receive_id": "", "content": json.dumps(content, ensure_ascii=False)}
        logger.debug("卡片 content: %s", json.dumps(content, ensure_ascii=False)[:3000])

        for to in sendtos:
            rid = (
                "email" if "@" in to else
                "chat_id" if to.startswith("oc_") else
                "open_id" if to.startswith("ou_") else
                "union_id" if to.startswith("on_") else
                "user_id"
            )
            body["receive_id"] = to
            resp = requests.post(
                f"https://{FEISHU_DOMAIN}/open-apis/im/v1/messages",
                headers=headers, params={"receive_id_type": rid},
                json=body, timeout=FEISHU_API_TIMEOUT
            )
            resp_data = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {}
            logger.info("发送卡片给 %s 类型=%s, 状态=%s, code=%s, msg=%s",
                        to, rid, resp.status_code, resp_data.get("code"), resp_data.get("msg"))

    if image_path:
        try:
            os.remove(image_path)
            logger.info("清理临时图片: %s", image_path)
        except Exception:
            pass

def main():
    logger.info("脚本启动")
    try:
        payload = load_payload()
        items = aggregate_events(payload)
        if not items:
            logger.info("未发现告警事件，退出")
            return

        cfg = payload.get("params", {})
        sendtos = payload.get("sendtos", [])
        
        # 应用 params 覆盖（params > 环境变量 > 默认值）
        apply_params_override(cfg)
        
        domain_url = cfg.get("domain_url", DEFAULT_DOMAIN_URL)

        send_cards(cfg.get("feishuapp_id", ""), cfg.get("feishuapp_secret", ""),
                   sendtos, items, domain_url, payload)
        logger.info("脚本结束")
    except SystemExit:
        raise
    except Exception as e:
        logger.error("主流程异常: %s", e, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
