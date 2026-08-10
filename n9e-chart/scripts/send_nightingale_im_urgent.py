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

# Grafana 图表渲染配置
GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "http://ifly.iflytek.com/grafana")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", """")
GRAFANA_DASHBOARD_UID = os.getenv("GRAFANA_DASHBOARD_UID", "adl6lsk")
GRAFANA_DATASOURCE_UID = os.getenv("GRAFANA_DATASOURCE_UID", "cefdos8p4hc74c")
GRAFANA_PANEL_ID = 1
GRAFANA_RENDER_WIDTH = 1000
GRAFANA_RENDER_HEIGHT = 500

# 回调服务地址（告警屏蔽、AI 分析、协同群功能）
CALLBACK_SERVER_URL = os.getenv("CALLBACK_SERVER_URL", "http://n9e-gateway.n9e.svc.cluster.local:5000")

# 飞书开放平台域名
FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "open.xfchat.iflytek.com")

# 超时配置（秒，可通过环境变量调整，用于避免脚本被 n9e 超时 kill）
GRAFANA_RENDER_TIMEOUT = int(os.getenv("GRAFANA_RENDER_TIMEOUT", "15"))  # Grafana 图片渲染 / 飞书图片上传
FEISHU_API_TIMEOUT = int(os.getenv("FEISHU_API_TIMEOUT", "6"))           # 飞书 API（token/发消息/加急）
GRAFANA_API_TIMEOUT = int(os.getenv("GRAFANA_API_TIMEOUT", "8"))         # Grafana 仪表盘 CRUD
CALLBACK_API_TIMEOUT = int(os.getenv("CALLBACK_API_TIMEOUT", "6"))       # 回调服务注册（屏蔽/AI分析/协同群）

# 重试策略
RETRY_MAX = 2
RETRY_DELAY_MIN = 1
RETRY_DELAY_MAX = 2

# ============================================================================
# 日志配置
# ============================================================================

# HTTP 调试开关（生产环境设为 0，可通过环境变量 HTTP_DEBUG 覆盖）
HTTPConnection.debuglevel = int(os.getenv("HTTP_DEBUG", "0"))

# 日志和临时文件目录（容器内建议挂载 emptyDir 或 PVC）
LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")
os.makedirs(LOG_DIR, exist_ok=True)
fh = TimedRotatingFileHandler(os.path.join(LOG_DIR, "send_nightingale_im_urgent.log"),
                              when='midnight', backupCount=7, encoding='utf-8')
fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(fmt); ch.setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh); logger.addHandler(ch)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)  # 关闭 urllib3 DEBUG 噪音

# 默认告警详情地址
DEFAULT_DOMAIN_URL = os.getenv("DEFAULT_DOMAIN_URL", "http://n9e-center.iflytek.com")

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


def _retry_sleep(attempt: int) -> None:
    if attempt >= RETRY_MAX - 1:
        return
    delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
    time.sleep(delay)


def _grafana_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {GRAFANA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _is_aggregated_payload(payload: Dict[str, Any]) -> bool:
    raw = payload.get('events') or [payload.get('event')]
    hosts = set()
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        inst = (ev.get('tags_map') or {}).get('instance')
        if inst:
            hosts.add(inst)
    return len(hosts) > 1


def _extract_promql(ev: Dict[str, Any]) -> str:
    prom_ql_raw = ev.get('prom_ql', '')
    if not prom_ql_raw:
        return ""
    instance = (ev.get('tags_map') or {}).get('instance', '')

    expr = prom_ql_raw.strip()
    parts = expr.split(' and ')
    parts2 = []
    for p in parts:
        p2 = re.sub(r'\s*[<>!=]=?\s*\d+\.?\d*\s*$', '', p.strip()).rstrip(' ,')
        parts2.append(p2 if p2 else p.strip())
    expr = ' and '.join(parts2)

    if instance:
        def add_instance(m):
            inner = m.group(1)
            if 'instance=' not in inner and 'instance!=' not in inner:
                inner = (inner + ',' if inner else '') + f'instance="{instance}"'
            return '{' + inner + '}'
        expr = re.sub(r'\{([^}]*)\}', add_instance, expr)
    return expr


def _get_dashboard() -> Optional[Dict[str, Any]]:
    url = f"{GRAFANA_BASE_URL}/api/dashboards/uid/{GRAFANA_DASHBOARD_UID}"
    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
            if r.status_code == 200:
                return r.json().get('dashboard', {})
        except Exception:
            logger.error("获取 Dashboard 异常", exc_info=True)
        _retry_sleep(attempt)
    return None


def _update_dashboard(dashboard: Dict[str, Any]) -> bool:
    url = f"{GRAFANA_BASE_URL}/api/dashboards/db"
    payload = {"dashboard": dashboard, "overwrite": True}
    for attempt in range(RETRY_MAX):
        try:
            r = requests.post(url, headers=_grafana_headers(), json=payload, timeout=GRAFANA_API_TIMEOUT, verify=False)
            if r.status_code == 200:
                return True
        except Exception:
            logger.error("更新 Dashboard 异常", exc_info=True)
        _retry_sleep(attempt)
    return False


def _render_panel_to_file(prom_ql: str, rule_name: str) -> Optional[str]:
    now_utc = datetime.now(timezone.utc)
    from_utc = now_utc - timedelta(minutes=5)
    from_str = from_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    temp_dashboard_uid = f"temp_urgent_{int(time.time() * 1000)}"

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
            r = requests.post(create_url, headers=_grafana_headers(), json=temp_dashboard, timeout=GRAFANA_API_TIMEOUT, verify=False)
            if r.status_code == 200:
                logger.info("临时 Dashboard 创建成功")
                break
        except Exception:
            logger.error("临时 Dashboard 创建异常", exc_info=True)
        _retry_sleep(attempt)
    else:
        logger.error("临时 Dashboard 创建失败")
        return None

    time.sleep(0.5)

    url = (
        f"{GRAFANA_BASE_URL}/render/d-solo/{temp_dashboard_uid}"
        f"?orgId=1&from={from_str}&to={to_str}&timezone=Asia/Shanghai"
        f"&panelId=1&__feature.dashboardSceneSolo=true"
        f"&width={GRAFANA_RENDER_WIDTH}&height={GRAFANA_RENDER_HEIGHT}&scale=1&tz=Asia/Shanghai"
        f"&theme=dark"
    )
    headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}", "Accept": "image/png"}
    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=headers, timeout=GRAFANA_RENDER_TIMEOUT, verify=False)
            if r.status_code == 200 and r.headers.get('Content-Type', '').startswith('image'):
                fp = os.path.join(LOG_DIR, f"urgent_alert_{int(time.time())}.png")
                with open(fp, 'wb') as f:
                    f.write(r.content)
                logger.info("图片渲染成功: %s", fp)
                _delete_temp_dashboard(temp_dashboard_uid)
                return fp
        except Exception:
            logger.error("渲染图片异常", exc_info=True)
        _retry_sleep(attempt)
    
    _delete_temp_dashboard(temp_dashboard_uid)
    return None


def _delete_temp_dashboard(uid: str):
    try:
        r = requests.delete(f"{GRAFANA_BASE_URL}/api/dashboards/uid/{uid}", headers=_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
        logger.info("删除临时 Dashboard %s: status=%s", uid, r.status_code)
    except Exception as e:
        logger.warning("删除临时 Dashboard %s 失败: %s", uid, e)


def _upload_image_to_feishu(tenant_token: str, image_path: str) -> str:
    url = f"https://{FEISHU_DOMAIN}/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {tenant_token}"}
    for attempt in range(RETRY_MAX):
        try:
            with open(image_path, 'rb') as f:
                files = {"image": ("alert_chart.png", f, "image/png")}
                data = {"image_type": "message"}
                r = requests.post(url, headers=headers, files=files, data=data, timeout=GRAFANA_RENDER_TIMEOUT)
            resp = r.json()
            if r.status_code == 200 and resp.get('code') == 0:
                return resp.get('data', {}).get('image_key', '')
        except Exception:
            logger.error("上传图片异常", exc_info=True)
        _retry_sleep(attempt)
    return ""


def generate_image_key(payload: Dict[str, Any], tenant_token: str) -> str:
    if _is_aggregated_payload(payload):
        return ""
    ev = (payload.get('events') or [payload.get('event')])[0]
    if not isinstance(ev, dict):
        return ""
    prom_ql = _extract_promql(ev)
    if not prom_ql:
        return ""

    rule_name = ev.get('rule_name', '告警图表')
    fp = _render_panel_to_file(prom_ql, rule_name)
    if not fp:
        return ""
    image_key = _upload_image_to_feishu(tenant_token, fp)
    try:
        os.remove(fp)
    except Exception:
        pass
    return image_key

def timeformat(ts: int) -> str:
    """将时间戳转换为可读字符串"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def build_mute_option_value(duration: str, group_id: int, group_name: str, rule_name: str,
                             instance: str, trigger_time: int, tags: List[str],
                             is_aggregated: bool = False, instances: List[str] = None) -> str:
    """构建屏蔽选项 value（向回调服务注册得到短 token）。

    避免飞书 select_static option value 长度限制导致 200340 错误，
    完整屏蔽参数先注册到回调服务，option value 只存短 token。
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

        try:
            reg_resp = requests.post(
                f"{CALLBACK_SERVER_URL}/mute/register",
                json=mute_data,
                timeout=CALLBACK_API_TIMEOUT
            )
            if reg_resp.status_code == 200:
                token = reg_resp.json().get("token", "")
                if token:
                    return token
        except Exception as e:
            logger.warning("屏蔽参数注册失败，回退 base64: %s", e)

        # 回退：base64 编码 JSON（飞书可能因长度拒绝）
        value_json = json.dumps(mute_data, ensure_ascii=False)
        return base64.b64encode(value_json.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"构建屏蔽选项 value 失败: {e}", exc_info=True)
        return ""

def get_access_token(app_id: str, app_secret: str) -> str:
    """获取飞书 access token"""
    url = f"https://{FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal"
    logger.debug("请求 Token：%s", {"app_id": app_id})
    try:
        r = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=FEISHU_API_TIMEOUT)
        data = r.json()
        logger.debug("Token 响应：%s", data)
        if r.status_code == 200 and data.get("msg") == "ok":
            return data["tenant_access_token"]
        logger.error("获取 Token 失败：%s", data)
    except Exception:
        logger.error("获取 Token 异常", exc_info=True)
    return ""

def load_payload():
    """从标准输入读取 JSON 数据"""
    try:
        payload = json.load(sys.stdin)
        logger.debug("✅ 成功读取告警原始数据: %s", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception:
        logger.error("❌ STDIN 解析失败，请确认输入为 JSON 格式")
        logger.debug(traceback.format_exc())
        sys.exit(1)

def aggregate_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    聚合事件数据，输出统一结构：
    包括 title, group, hosts, host_names, event_ids, notes, time, severity, is_recover, event_id（如存在）
    """
    raw = payload.get('events') or [payload.get('event')]
    agg = {}
    for ev in raw:
        if not isinstance(ev, dict):
            logger.warning("跳过非字典事件: %r", ev)
            continue

        tags = ev.get('tags_map', {})
        title = payload.get('tpl', {}).get('title') or ev.get('rule_name', '')
        group = tags.get('group') or ev.get('group_name', '')
        # 如果 group 为空，尝试使用 origin_prometheus
        origin_prometheus = tags.get('origin_prometheus') or ev.get('origin_prometheus', '')
        if not group and origin_prometheus:
            group = origin_prometheus
        is_recover = ev.get('is_recovered', False)
        # 将 origin_prometheus 加入聚合判断
        key = (title, group, origin_prometheus, is_recover)

        inst = tags.get('instance', '')
        host_name = tags.get('name', '')  # 新增：获取主机名称
        note = ev.get('rule_note') or ev.get('annotations', {}).get('description', '')
        # 恢复事件使用 last_eval_time，告警事件使用 trigger_time
        trigger_time = ev.get('trigger_time', 0)
        last_eval_time = ev.get('last_eval_time', 0)
        ts = last_eval_time if is_recover else trigger_time
        tstr = timeformat(int(ts)) if ts else ''
        trigger_tstr = timeformat(int(trigger_time)) if trigger_time else ''
        sev = ev.get('severity', 0)

        # 从 ev 中获取 event_id，如果有字段 'id'
        event_id = None
        if 'id' in ev:
            # 支持数字或列表形式
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

        # 处理主机地址显示格式
        if not hosts:
            hosts_str = "N/A"
        elif len(hosts) == 1:
            hosts_str = hosts[0]
        else:
            hosts_str = f"{';'.join(hosts)} 共计 {len(hosts)} 台"

        # 处理主机名称显示格式
        if not host_names:
            host_names_str = ""
        else:
            host_names_str = ";".join(host_names)

        # 处理事件ID显示格式
        event_ids_str = ";".join(event_ids) if event_ids else ""

        # 是否聚合告警（多台主机）
        is_aggregated = len(hosts) > 1

        results.append({
            "title": v["title"],
            "group": v["group"],
            "is_origin_prometheus": v.get("is_origin_prometheus", False),
            "hosts": hosts_str,
            "host_names": host_names_str,
            "event_ids": event_ids_str,
            "notes": notes[0] if len(notes) == 1 else "; ".join(notes),
            "time": v["time"],
            "trigger_time": v["trigger_time"],
            "severity": v["severity"],
            "is_recover": v["is_recover"],
            "event_id": v.get("event_id"),
            "is_aggregated": is_aggregated
        })
    return results

def build_markdown(item: Dict[str, Any]) -> str:
    """根据事件信息构建 Markdown 文本"""
    is_recover = item["is_recover"]
    lines = []
    lines.append(f"**告警级别:** S{item['severity']} {'恢复' if is_recover else '告警'}  ")
    if item["group"]:
        # 如果使用了 origin_prometheus，显示"集群分组"，否则显示"业务分组"
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

# 全局变量：记录权限检查状态
_permission_checked = False
_has_email_to_user_id_permission = False

def get_user_id_by_email(token: str, email: str, user_id_type: str = "open_id") -> Optional[str]:
    """
    通过 email 获取用户ID（open_id/union_id/user_id）
    接口文档：https://{FEISHU_DOMAIN}/open-apis/contact/v3/users/batch_get_id

    参数:
        token: tenant_access_token
        email: 用户邮箱（注意：不支持企业邮箱，必须使用用户个人邮箱）
        user_id_type: 要获取的用户ID类型，可选值：open_id, union_id, user_id，默认 open_id

    返回:
        str: 用户ID，如果获取失败返回 None

    注意事项（参考官方文档）：
    - 请求后不返回用户ID的可能原因：
      1. tenant_access_token 有误或应用不一致
      2. 输入的邮箱不存在
      3. 应用未开通「通过手机号或邮箱获取用户 ID」权限
      4. 应用无权限查看用户信息（需要在应用详情页配置数据权限）
      5. 使用企业邮箱查询（不支持）
      6. 用户已离职（如果 include_resigned 为 false）
    """
    global _permission_checked, _has_email_to_user_id_permission

    # 如果已经检查过权限且权限不足，直接返回 None，避免重复尝试
    if _permission_checked and not _has_email_to_user_id_permission:
        return None

    url = f"https://{FEISHU_DOMAIN}/open-apis/contact/v3/users/batch_get_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    body = {"emails": [email]}
    params = {"user_id_type": user_id_type}  # 查询参数，默认 open_id

    logger.debug("通过 email 获取用户ID，email=%s, user_id_type=%s", email, user_id_type)

    try:
        resp = requests.post(url, headers=headers, params=params, json=body, timeout=FEISHU_API_TIMEOUT)
        data = resp.json()

        if resp.status_code == 200 and data.get("code") == 0:
            user_list = data.get("data", {}).get("user_list", [])
            if user_list and len(user_list) > 0:
                user_id = user_list[0].get("user_id")
                if user_id:
                    logger.debug("成功获取 email %s 对应的用户ID: %s (类型: %s)", email, user_id, user_id_type)
                    _has_email_to_user_id_permission = True
                    _permission_checked = True
                    return user_id
            # 检查是否有返回用户列表
            if not user_list or len(user_list) == 0:
                logger.warning("email %s 未找到对应的用户ID（可能原因：用户不存在、已离职、使用企业邮箱等）", email)
            else:
                logger.warning("email %s 返回的用户列表为空", email)
        else:
            error_msg = data.get("msg", "未知错误")
            error_code = data.get("code", resp.status_code)

            # 检查是否是权限不足的错误（99991672 或其他权限相关错误）
            if error_code == 99991672 or "scope" in error_msg.lower() or "permission" in error_msg.lower() or "access denied" in error_msg.lower():
                if not _permission_checked:
                    logger.error("❌ 应用缺少通过 email 获取用户ID的权限，错误码=%s, 错误信息=%s", error_code, error_msg)
                    logger.error("   可能的原因：")
                    logger.error("   1. 应用未开通「通过手机号或邮箱获取用户 ID」权限")
                    logger.error("   2. 应用无权限查看用户信息（需要在应用详情页配置数据权限）")
                    logger.error("   3. tenant_access_token 有误或对应的应用不一致")
                    logger.error("   解决方案：")
                    logger.error("   - 在飞书开发者后台为应用申请权限：通过手机号或邮箱获取用户 ID")
                    logger.error("   - 或者在 params.email_to_user_id_map 中配置映射表（推荐）")
                    _has_email_to_user_id_permission = False
                    _permission_checked = True
                return None
            else:
                # 其他错误情况
                logger.warning("获取 email %s 对应的用户ID失败，code=%s, msg=%s", email, error_code, error_msg)
                # 检查是否是邮箱相关的问题
                if "email" in error_msg.lower() or "mail" in error_msg.lower():
                    logger.warning("   提示：确保使用的是用户个人邮箱，不支持企业邮箱")
                elif "resigned" in error_msg.lower() or "离职" in error_msg:
                    logger.warning("   提示：用户可能已离职")
    except Exception:
        logger.error("获取 email %s 对应的用户ID异常", email, exc_info=True)

    return None

def send_urgent_phone(token: str, message_id: str, user_id_list: List[str],
                      user_id_type: str = "open_id") -> bool:
    """
    发送电话加急

    参数:
        token: tenant_access_token
        message_id: 待加急的消息 ID
        user_id_list: 加急的目标用户 ID 列表
        user_id_type: 用户 ID 类型，可选值：open_id, union_id, user_id，默认 open_id

    返回:
        bool: 是否成功
    """
    if not message_id or not user_id_list:
        logger.warning("message_id 或 user_id_list 为空，跳过电话加急")
        return False

    url = f"https://{FEISHU_DOMAIN}/open-apis/im/v1/messages/{message_id}/urgent_phone"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    params = {"user_id_type": user_id_type}
    body = {"user_id_list": user_id_list}

    logger.info("发送电话加急，message_id=%s, user_id_list=%s, user_id_type=%s",
                message_id, user_id_list, user_id_type)

    try:
        resp = requests.patch(url, headers=headers, params=params, json=body, timeout=FEISHU_API_TIMEOUT)
        data = resp.json()

        if resp.status_code == 200 and data.get("code") == 0:
            invalid_list = data.get("data", {}).get("invalid_user_id_list", [])
            if invalid_list:
                logger.warning("部分用户ID无效，已跳过：%s", invalid_list)
            logger.info("✅ 电话加急发送成功，message_id=%s", message_id)
            return True
        else:
            error_msg = data.get("msg", "未知错误")
            error_code = data.get("code", resp.status_code)
            logger.error("❌ 电话加急发送失败，message_id=%s, code=%s, msg=%s",
                        message_id, error_code, error_msg)

            # 记录常见错误码的说明
            error_codes = {
                230001: "参数错误",
                230002: "机器人不在对应群组中",
                230006: "应用未启用机器人能力",
                230012: "机器人不是消息的发送者",
                230013: "目标用户不在机器人可用范围内",
                230023: "用户未读的加急消息过多（超过200条）",
                230024: "加急额度超限",
                230027: "暂不支持在外部群中进行本操作",
                230052: "无权加急或被鉴别为风险操作",
                230098: "被聚合的消息不支持加急",
                230110: "消息已删除",
                232009: "相关群组已被解散"
            }
            if error_code in error_codes:
                logger.error("错误说明：%s", error_codes[error_code])

            return False
    except Exception:
        logger.error("电话加急发送异常，message_id=%s", message_id, exc_info=True)
        return False

def get_receive_id_type(receive_id: str) -> str:
    """
    识别接收者ID类型

    返回: "email", "chat_id", "open_id", "union_id", "user_id"
    """
    if "@" in receive_id:
        return "email"
    elif receive_id.startswith("oc_"):
        return "chat_id"
    elif receive_id.startswith("ou_"):
        return "open_id"
    elif receive_id.startswith("on_"):
        return "union_id"
    else:
        return "user_id"

def send_cards(app_id: str, app_secret: str, sendtos: List[str],
               items: List[Dict[str, Any]], domain_url: str,
               email_to_user_id_map: Optional[Dict[str, str]] = None,
               image_key: str = "",
               payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    发送飞书卡片，自动附带'告警屏蔽'下拉框和'AI分析'按钮，并支持电话加急
    电话加急会根据 sendtos 自动提取用户ID类型的接收者（open_id/union_id/user_id）进行加急
    自动识别接收者ID类型，并根据类型设置查询参数 user_id_type

    参数:
        app_id: 应用ID
        app_secret: 应用密钥
        sendtos: 接收者列表（推荐使用用户ID类型，如 ou_xxx/open_id, on_xxx/union_id, 或 user_id）
        items: 事件项列表
        domain_url: 告警详情域名
        email_to_user_id_map: email 到用户ID的映射字典（可选），例如 {"email@example.com": "ou_xxxxx"}
        payload: 原始 payload，用于构造告警屏蔽下拉框

    返回:
        List[Dict[str, Any]]: 发送成功的消息ID列表，格式为 [{"receive_id": "xxx", "message_id": "xxx", "receive_id_type": "xxx"}, ...]
    """
    token = get_access_token(app_id, app_secret)
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    message_ids = []

    # 图片生成在 main() 中完成，这里仅使用传入的 image_key。

    for item in items:
        is_recover = item["is_recover"]
        status_flag = "﹝恢复﹞" if is_recover else "﹝告警﹞"
        header_text = f"信息化监控告警 🔥 {status_flag} {item['group']}- {item['title']}"
        tpl = "green" if is_recover else "red"
        md = build_markdown(item)

        # 优先使用传入的 event_id 字段
        event_id = item.get("event_id")
        if event_id:
            logger.debug("使用 event_id：%s", event_id)
        else:
            notes = item.get("notes", "")
            # 回退：从 note 中正则提取
            match = re.search(r'alert-his-events/([^\s;]+)', notes)
            if match:
                event_id = match.group(1)
                logger.debug("从 notes 提取到 event_id：%s", event_id)

        elements = []
        if image_key and not item.get("is_aggregated", False):
            elements.append({"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "监控图表"}})
        elements.append({"tag": "markdown", "content": md})

        # 告警屏蔽下拉框 + AI分析按钮（合并到同一个 action 容器，同一行显示）
        action_items = []

        # 告警屏蔽下拉框
        if payload:
            try:
                raw_events = payload.get('events') or [payload.get('event')]
                if raw_events and isinstance(raw_events[0], dict):
                    first_event = raw_events[0]
                    group_id = first_event.get('group_id', 0)
                    group_name = first_event.get('group_name', '')
                    rule_name_for_mute = first_event.get('rule_name', '')
                    trigger_time = first_event.get('trigger_time', 0)
                    tags_list = first_event.get('tags', [])
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
                            instance, trigger_time, tags_list, is_aggregated, instances
                        )
                        if option_value:
                            mute_options.append({
                                "text": {"tag": "plain_text", "content": label},
                                "value": option_value
                            })

                    if mute_options:
                        action_items.append({
                            "tag": "select_static",
                            "placeholder": {"tag": "plain_text", "content": "告警屏蔽"},
                            "value": {"action": "mute_alert"},
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
                        'prom_ql':      _extract_promql(first_event),
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

        if action_items:
            elements.append({"tag": "action", "actions": action_items})

        content = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": header_text}, "template": tpl},
            "elements": elements
        }
        body = {"msg_type": "interactive", "receive_id": "", "content": json.dumps(content, ensure_ascii=False)}

        for to in sendtos:
            # 自动识别接收者ID类型
            rid = get_receive_id_type(to)
            body["receive_id"] = to
            try:
                resp = requests.post(
                f"https://{FEISHU_DOMAIN}/open-apis/im/v1/messages",
                    headers=headers,
                    params={"receive_id_type": rid},
                    json=body,
                    timeout=FEISHU_API_TIMEOUT
                )
                resp_data = resp.json()
                logger.info("发送给 %s 类型=%s, 状态=%s", to, rid, resp.status_code)

                # 获取消息ID
                if resp.status_code == 200 and resp_data.get("code") == 0:
                    message_id = resp_data.get("data", {}).get("message_id", "")
                    if message_id:
                        logger.info("✅ 消息发送成功，message_id=%s, receive_id=%s, 类型=%s", message_id, to, rid)
                        message_ids.append({"receive_id": to, "message_id": message_id, "receive_id_type": rid})

                        # 自动电话加急：从 sendtos 自动提取用户ID类型的接收者进行加急
                        # 自动识别接收者ID类型，并根据类型设置查询参数 user_id_type
                        if not is_recover:
                            urgent_user_id = None
                            urgent_user_id_type = None

                            # 根据接收者ID类型自动判断 user_id_type 查询参数
                            if rid == "open_id":
                                # 接收者是 open_id 类型（以 ou_ 开头）
                                urgent_user_id = to
                                urgent_user_id_type = "open_id"
                            elif rid == "union_id":
                                # 接收者是 union_id 类型（以 on_ 开头）
                                urgent_user_id = to
                                urgent_user_id_type = "union_id"
                            elif rid == "user_id":
                                # 接收者是 user_id 类型（其他格式）
                                urgent_user_id = to
                                urgent_user_id_type = "user_id"
                            elif rid == "email":
                                # 接收者是 email，自动通过 API 获取用户ID
                                # 注意：电话加急API不支持直接使用email，必须使用用户ID类型（open_id/union_id/user_id）

                                # 优先从映射表中查找（如果配置了映射）
                                if email_to_user_id_map and to in email_to_user_id_map:
                                    urgent_user_id = email_to_user_id_map.get(to)
                                    if urgent_user_id:
                                        # 自动识别映射后的用户ID类型
                                        mapped_rid = get_receive_id_type(urgent_user_id)
                                        if mapped_rid in ["open_id", "union_id", "user_id"]:
                                            urgent_user_id_type = mapped_rid
                                            logger.debug("从映射中找到 email %s 对应的用户ID: %s (类型: %s)",
                                                        to, urgent_user_id, urgent_user_id_type)
                                        else:
                                            logger.warning("email %s 映射到的用户ID格式不正确: %s，尝试通过 API 获取", to, urgent_user_id)
                                            urgent_user_id = None

                                # 如果映射表中没有或获取失败，通过 API 自动获取
                                if not urgent_user_id:
                                    # 检查是否已经知道权限不足，如果是，直接跳过
                                    if _permission_checked and not _has_email_to_user_id_permission:
                                        logger.debug("应用缺少通过 email 获取用户ID的权限，跳过 email %s 的电话加急（建议配置 email_to_user_id_map 映射表）", to)
                                    else:
                                        logger.info("自动通过 API 获取 email %s 对应的用户ID（open_id）", to)
                                        # 默认使用 open_id 类型（推荐）
                                        urgent_user_id = get_user_id_by_email(token, to, "open_id")
                                        if urgent_user_id:
                                            urgent_user_id_type = "open_id"
                                        else:
                                            # 如果 open_id 获取失败且权限已检查，不再尝试 user_id
                                            if _permission_checked and not _has_email_to_user_id_permission:
                                                logger.debug("权限不足，跳过 user_id 尝试")
                                            else:
                                                # 如果 open_id 获取失败，尝试 user_id
                                                logger.debug("使用 open_id 获取失败，尝试使用 user_id 获取 email %s 对应的用户ID", to)
                                                urgent_user_id = get_user_id_by_email(token, to, "user_id")
                                                if urgent_user_id:
                                                    urgent_user_id_type = "user_id"

                                        if not urgent_user_id:
                                            if not _permission_checked:
                                                logger.warning("无法通过 API 获取 email %s 对应的用户ID，跳过电话加急", to)
                            elif rid == "chat_id":
                                # 群组不支持电话加急
                                logger.debug("接收者 %s 类型为 chat_id（群组），跳过电话加急", to)

                            # 执行电话加急（自动使用识别到的 user_id_type 作为查询参数）
                            if urgent_user_id and urgent_user_id_type:
                                logger.info("自动识别接收者类型并开始电话加急，message_id=%s, 接收者=%s, 用户ID=%s, 查询参数 user_id_type=%s",
                                           message_id, to, urgent_user_id, urgent_user_id_type)
                                send_urgent_phone(token, message_id, [urgent_user_id], urgent_user_id_type)
                        elif is_recover:
                            logger.debug("恢复事件跳过电话加急，message_id=%s", message_id)
                    else:
                        logger.warning("消息发送成功但未获取到 message_id，receive_id=%s", to)
                else:
                    error_msg = resp_data.get("msg", "未知错误")
                    logger.error("❌ 消息发送失败，receive_id=%s, code=%s, msg=%s",
                                to, resp_data.get("code", resp.status_code), error_msg)
            except Exception:
                logger.error("发送消息异常，receive_id=%s", to, exc_info=True)

    return message_ids

def main():
    """主入口：读取 stdin，处理 payload，调用 send_cards 并发送电话加急"""

    logger.info("脚本启动（电话加急版本）")
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

    # 生成图片并上传（返回 image_key），聚合告警/恢复告警自动跳过
    image_key = ""
    try:
        if cfg.get("feishuapp_id") and cfg.get("feishuapp_secret"):
            token = get_access_token(cfg.get("feishuapp_id", ""), cfg.get("feishuapp_secret", ""))
            if token:
                image_key = generate_image_key(payload, token)
    except Exception:
        logger.error("生成告警图片异常", exc_info=True)

    # 读取 email 到用户ID的映射配置
    email_to_user_id_map = cfg.get("email_to_user_id_map", {})  # email -> user_id 映射

    # 自动从 sendtos 中提取用户ID类型的接收者，并根据ID类型自动识别查询参数 user_id_type
    user_id_receivers = []
    user_id_receivers_by_type = {"open_id": [], "union_id": [], "user_id": []}
    email_receivers_with_map = []
    for to in sendtos:
        rid = get_receive_id_type(to)
        if rid in ["open_id", "union_id", "user_id"]:
            user_id_receivers.append(to)
            user_id_receivers_by_type[rid].append(to)
        elif rid == "email" and email_to_user_id_map and to in email_to_user_id_map:
            email_receivers_with_map.append(to)

    # 统计 email 类型的接收者
    email_receivers = [to for to in sendtos if "@" in to]

    if user_id_receivers:
        logger.info("从 sendtos 自动识别到 %d 个用户ID类型的接收者，将自动使用对应的 user_id_type 查询参数进行电话加急：", len(user_id_receivers))
        for rid_type in ["open_id", "union_id", "user_id"]:
            if user_id_receivers_by_type[rid_type]:
                logger.info("  - %s 类型（user_id_type=%s）：%s",
                           rid_type, rid_type, user_id_receivers_by_type[rid_type])
    if email_receivers:
        logger.info("发现 %d 个 email 类型的接收者，将尝试自动通过 API 获取对应的用户ID进行电话加急：%s",
                   len(email_receivers), email_receivers)
        if email_to_user_id_map:
            mapped_count = len([e for e in email_receivers if e in email_to_user_id_map])
            if mapped_count > 0:
                logger.info("  其中 %d 个 email 已配置映射表，将优先使用映射，其余将尝试通过 API 获取", mapped_count)
            else:
                logger.debug("  映射表中未找到匹配的 email，将全部尝试通过 API 自动获取用户ID")
        else:
            logger.info("  未配置 email_to_user_id_map 映射表，将尝试通过 API 获取用户ID")
            logger.info("  注意：如果 API 获取失败（权限不足等），建议配置映射表（在 params.email_to_user_id_map 中）")
            logger.info("  提示：确保使用用户个人邮箱，不支持企业邮箱")
    if not user_id_receivers and not email_receivers:
        logger.info("未发现可进行电话加急的接收者（仅发现 chat_id 类型）")

    message_ids = send_cards(
        cfg.get("feishuapp_id", ""),
        cfg.get("feishuapp_secret", ""),
        sendtos,
        items,
        domain_url,
        email_to_user_id_map=email_to_user_id_map if email_to_user_id_map else None,
        image_key=image_key,
        payload=payload,
    )

    if message_ids:
        logger.info("共发送 %d 条消息，已尝试电话加急", len(message_ids))

    logger.info("脚本结束")

if __name__ == "__main__":
    main()
