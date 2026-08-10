#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_nightingale_email.py
────────────────────────────────────────
• 按 (title, group, cluster, is_recover) 聚合；每组单独发信
• 邮件加入「随机渐变 + CSS 动效」背景，呈现高端动感视觉
"""

import sys
import os
import json
import html
import logging
import traceback
import smtplib
import random          # 用于随机生成渐变色
import time
from datetime import datetime
from typing import Optional
from logging.handlers import TimedRotatingFileHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.header import Header

import requests
import re
from datetime import timezone, timedelta
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

# 超时配置（秒，可通过环境变量调整，用于避免脚本被 n9e 超时 kill）
GRAFANA_RENDER_TIMEOUT = int(os.getenv("GRAFANA_RENDER_TIMEOUT", "15"))  # Grafana 图片渲染
GRAFANA_API_TIMEOUT = int(os.getenv("GRAFANA_API_TIMEOUT", "8"))        # Grafana 仪表盘 CRUD
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "10"))                      # SMTP 邮件发送

RETRY_MAX = 3
RETRY_DELAY_MIN = 3
RETRY_DELAY_MAX = 10


def _retry_sleep(attempt: int) -> None:
    if attempt >= RETRY_MAX - 1:
        return
    delay = RETRY_DELAY_MIN + (RETRY_DELAY_MAX - RETRY_DELAY_MIN) * attempt / (RETRY_MAX - 1)
    time.sleep(delay)


def _is_aggregated_payload(payload: dict) -> bool:
    raw = payload.get('events') or [payload.get('event')]
    hosts = set()
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        inst = (ev.get('tags_map') or {}).get('instance')
        if inst:
            hosts.add(inst)
    return len(hosts) > 1


def _extract_promql(ev: dict) -> str:
    prom_ql_raw = ev.get('prom_ql', '')
    if not prom_ql_raw:
        return ""
    instance = (ev.get('tags_map') or {}).get('instance', '')

    expr = prom_ql_raw.strip()
    parts = expr.split(' and ')
    parts = [re.sub(r'\s*[<>!=]=?\s*\d+\.?\d*\s*$', '', p.strip()).rstrip(' ,') or p.strip() for p in parts]
    expr = ' and '.join(parts)

    if instance:
        def add_instance(m):
            inner = m.group(1)
            if 'instance=' not in inner and 'instance!=' not in inner:
                inner = (inner + ',' if inner else '') + f'instance="{instance}"'
            return '{' + inner + '}'
        expr = re.sub(r'\{([^}]*)\}', add_instance, expr)
    return expr


def _grafana_headers() -> dict:
    return {
        "Authorization": f"Bearer {GRAFANA_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get_dashboard() -> Optional[dict]:
    url = f"{GRAFANA_BASE_URL}/api/dashboards/uid/{GRAFANA_DASHBOARD_UID}"
    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
            if r.status_code == 200:
                return r.json().get('dashboard')
        except Exception:
            logger.error("获取 Dashboard 异常", exc_info=True)
        _retry_sleep(attempt)
    return None


def _update_dashboard(dashboard: dict) -> bool:
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
    from_utc = now_utc - timedelta(hours=6)
    from_str = from_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    temp_dashboard_uid = f"temp_email_{int(time.time() * 1000)}"

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

    time.sleep(2)

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
        "User-Agent": "Mozilla/5.0",
    }

    for attempt in range(RETRY_MAX):
        try:
            r = requests.get(url, headers=headers, timeout=GRAFANA_RENDER_TIMEOUT, verify=False)
            if r.status_code == 200 and r.headers.get('Content-Type', '').startswith('image'):
                ts = int(time.time())
                fp = os.path.join(LOG_DIR, f"email_alert_{ts}.png")
                with open(fp, 'wb') as f:
                    f.write(r.content)
                logger.info("✅ Grafana 图片渲染成功: %s", fp)
                _delete_temp_dashboard(temp_dashboard_uid)
                return fp
        except requests.Timeout:
            logger.warning("⏱ Grafana 渲染超时 (%ds)，尝试 %d/%d", GRAFANA_RENDER_TIMEOUT, attempt+1, RETRY_MAX)
        except Exception as e:
            logger.error("❌ Grafana 渲染异常: %s", e, exc_info=True)
        _retry_sleep(attempt)
    
    _delete_temp_dashboard(temp_dashboard_uid)
    return None


def _delete_temp_dashboard(uid: str):
    try:
        r = requests.delete(f"{GRAFANA_BASE_URL}/api/dashboards/uid/{uid}", headers=_grafana_headers(), timeout=GRAFANA_API_TIMEOUT, verify=False)
        logger.info("删除临时 Dashboard %s: status=%s", uid, r.status_code)
    except Exception as e:
        logger.warning("删除临时 Dashboard %s 失败: %s", uid, e)


def generate_email_alert_image(payload: dict) -> str:
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
    return fp or ""

# ============================================================================
# 运行时配置：日志和邮件服务器
# ============================================================================

# 调试模式（True 时邮件保存到 /tmp 便于本地预览，不实际发送）
DEBUG_MODE = False

# 日志和临时文件目录（容器内建议挂载 emptyDir 或 PVC）
LOG_DIR = os.getenv("LOG_DIR", "/data/n9e/alerts")

# SMTP 邮件服务器配置
EMAIL_HOST = os.getenv("EMAIL_HOST", "mail.iflymail.com.cn")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER", "qxb_super@iflymail.com.cn")
EMAIL_PASS = os.getenv("MAIL_PASS", "vpcJXDStkeMvrz4U")
EMAIL_FROM = os.getenv("EMAIL_FROM", EMAIL_USER)

# ============================================================================


# ============================================================================
# params 覆盖机制（优先级: params > 环境变量 > 默认值）
# ============================================================================

def apply_params_override(params: dict) -> None:
    """
    从 payload.params 覆盖全局配置变量（如果 params 有配置，优先级最高）
    保持向后兼容：n9e 告警渠道通过 params 下发的配置优先于容器环境变量
    """
    global GRAFANA_BASE_URL, GRAFANA_TOKEN, GRAFANA_DASHBOARD_UID, GRAFANA_DATASOURCE_UID
    global EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_FROM

    if params.get("grafana_base_url"):
        GRAFANA_BASE_URL = params["grafana_base_url"]
    if params.get("grafana_token"):
        GRAFANA_TOKEN = params["grafana_token"]
    if params.get("grafana_dashboard_uid"):
        GRAFANA_DASHBOARD_UID = params["grafana_dashboard_uid"]
    if params.get("grafana_datasource_uid"):
        GRAFANA_DATASOURCE_UID = params["grafana_datasource_uid"]
    if params.get("email_host"):
        EMAIL_HOST = params["email_host"]
    if params.get("email_port"):
        EMAIL_PORT = int(params["email_port"])
    if params.get("email_user"):
        EMAIL_USER = params["email_user"]
    if params.get("email_pass"):
        EMAIL_PASS = params["email_pass"]
    if params.get("email_from"):
        EMAIL_FROM = params["email_from"]

# ============================================================================

# ───────── 日志 ─────────
os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, "send_nightingale_email.log")
_fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

fh = TimedRotatingFileHandler(log_path, when='midnight', interval=1,
                              backupCount=7, encoding='utf-8')
fh.setFormatter(_fmt)
fh.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(_fmt)
ch.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(fh)
logger.addHandler(ch)

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def load_payload() -> dict:
    """读取 stdin 并解析 JSON"""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        logger.debug("✅ 成功读取告警原始数据:\n%s", json.dumps(data, ensure_ascii=False, indent=2))
        return data
    except Exception:
        logger.error("❌ 解析 WebHook payload 失败")
        logger.debug(traceback.format_exc())
        sys.exit(1)


def _to_bool(val) -> bool:
    """将 0/1/True/False/'true'/'1' 等转成布尔"""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "y")
    return False


def aggregate_events(payload: dict) -> list[dict]:
    """
    根据 (title, group, is_recover) 聚合
    返回排序后的列表
    """
    raw_events = payload.get("events") or [payload.get("event")]
    agg: dict[tuple, dict] = {}

    for ev in raw_events:
        if not isinstance(ev, dict):
            logger.warning("⚠️ 非 dict 事件已跳过: %r", ev)
            continue

        tags = ev.get("tags_map") or {}
        title   = payload.get("tpl", {}).get("title") or ev.get("rule_name", "")
        group   = tags.get("group")  or ev.get("group_name") or ""
        # 如果 group 为空，尝试使用 origin_prometheus
        origin_prometheus = tags.get("origin_prometheus") or ev.get("origin_prometheus", "")
        if not group and origin_prometheus:
            group = origin_prometheus
        if not group:
            group = "default"
        is_recover = _to_bool(ev.get("is_recovered"))
        # 将 origin_prometheus 加入聚合判断
        key = (title, group, origin_prometheus, is_recover)

        sev_raw = ev.get("severity", 99)
        try:
            severity = int(sev_raw)
        except Exception:
            severity = 99

        # 恢复事件使用 last_eval_time，告警事件使用 trigger_time
        trigger_ts = ev.get("trigger_time")
        last_eval_ts = ev.get("last_eval_time")
        ts = last_eval_ts if is_recover else trigger_ts
        time_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S") if ts else "N/A"
        trigger_time_str = datetime.fromtimestamp(int(trigger_ts)).strftime("%Y-%m-%d %H:%M:%S") if trigger_ts else "N/A"

        host = tags.get("instance", "N/A")
        host_name = tags.get("name", "")  # 新增：获取主机名称
        note_src = ev.get("rule_note") or ev.get("annotations", {}).get("description", "")
        note_html = html.escape(note_src).replace("\n", "<br>")

        # 获取事件ID
        event_id = None
        if 'id' in ev:
            if isinstance(ev['id'], list) and ev['id']:
                event_id = str(ev['id'][0])
            else:
                event_id = str(ev['id'])

        if key not in agg:
            agg[key] = {
                "severity": severity,
                "time": time_str,
                "trigger_time": trigger_time_str,
                "items": [],
                "is_origin_prometheus": bool(not (tags.get("group") or ev.get("group_name")) and origin_prometheus)
            }
        agg[key]["items"].append({"host": host, "host_name": host_name, "note": note_html, "event_id": event_id})

    # 转为列表并排序（告警→恢复，再按严重级别、时间）
    res = [{
        "title": k[0], "group": k[1], "is_recover": k[3],
        "is_origin_prometheus": v.get("is_origin_prometheus", False),
        "severity": v["severity"], "time": v["time"], "trigger_time": v["trigger_time"], "items": v["items"]
    } for k, v in agg.items()]

    res.sort(key=lambda x: (x["is_recover"], x["severity"], x["time"]))
    logger.debug("✅ 聚合后:\n%s", json.dumps(res, ensure_ascii=False, indent=2))
    return res


# ─────────────────────────────────────────────
# 视觉生成：随机渐变 + 动画
# ─────────────────────────────────────────────
def gen_random_gradient(num_colors: int = None) -> tuple[str, str]:
    """
    生成彩虹色+天空色混合渐变 CSS 片段（每次随机生成10~16个不重复的柔和明亮色彩）
    返回 (linear-gradient字符串, 降级纯色)
    """
    import random
    # 每次随机生成10~16个颜色
    if num_colors is None:
        num_colors = random.randint(10, 16)

    def random_rainbow_sky_hex(existing_colors=None):
        if existing_colors is None:
            existing_colors = set()
        tries = 0
        while True:
            # 随机选择颜色类型：天空色(60%) 或 彩虹色(40%)
            if random.random() < 0.6:
                # 天空色：浅蓝、浅青、浅紫
                r = random.randint(180, 240)
                g = random.randint(200, 255)
                b = random.randint(220, 255)
            else:
                # 彩虹色：柔和的红、橙、黄、绿、蓝、紫
                color_type = random.choice(['red', 'orange', 'yellow', 'green', 'blue', 'purple'])
                if color_type == 'red':
                    r, g, b = random.randint(240, 255), random.randint(180, 220), random.randint(180, 220)
                elif color_type == 'orange':
                    r, g, b = random.randint(240, 255), random.randint(200, 240), random.randint(160, 200)
                elif color_type == 'yellow':
                    r, g, b = random.randint(240, 255), random.randint(240, 255), random.randint(180, 220)
                elif color_type == 'green':
                    r, g, b = random.randint(180, 220), random.randint(240, 255), random.randint(180, 220)
                elif color_type == 'blue':
                    r, g, b = random.randint(160, 200), random.randint(200, 240), random.randint(240, 255)
                elif color_type == 'purple':
                    r, g, b = random.randint(200, 240), random.randint(180, 220), random.randint(240, 255)

            color = f"#{r:02x}{g:02x}{b:02x}"
            if color not in existing_colors:
                return color
            tries += 1
            if tries > 20:
                return color

    colors = set()
    while len(colors) < num_colors:
        colors.add(random_rainbow_sky_hex(colors))
    colors = list(colors)
    angle = random.randint(0, 360)
    gradient_css = f"linear-gradient({angle}deg, {', '.join(colors)})"
    fallback = colors[0]
    return gradient_css, fallback


def build_email(one: dict, recipients: list[str], image_path: str = "") -> MIMEText:
    """
    构建带随机动画背景的邮件。
    - 主机地址单行显示，用分号分隔
    - 主机名称单行显示，用分号分隔（如果存在）
    - 事件ID单行显示，用分号分隔（如果存在）
    - 告警描述用 <ul> 展示，自动去重
    """
    status_flag = "﹝恢复﹞" if one["is_recover"] else "﹝告警﹞"
    subject = f"信息化监控告警 🔥 {status_flag} {one['group']}- {one['title']}"
    send_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 去重并拼接主机地址为一行（用分号隔开）
    hosts = sorted(set(h["host"] for h in one["items"] if h.get("host")))
    if not hosts:
        host_line = "N/A"
    elif len(hosts) == 1:
        host_line = hosts[0]
    else:
        host_line = f"{';'.join(hosts)} 共计 {len(hosts)} 台"

    # 去重并拼接主机名称为一行（用分号隔开），过滤空值
    host_names = sorted(set(h["host_name"] for h in one["items"] if h.get("host_name")))
    host_name_line = ";".join(host_names) if host_names else ""

    # 去重并拼接事件ID为一行（用分号隔开），过滤空值
    event_ids = sorted(set(h["event_id"] for h in one["items"] if h.get("event_id")))
    event_id_line = ";".join(event_ids) if event_ids else ""

    # 告警描述去重，按 <li> 展示
    notes = sorted(set(h["note"] for h in one["items"] if h.get("note")))

    # 渐变背景
    gradient_css, fallback_color = gen_random_gradient()

    # 构造 HTML 邮件内容（网页端渐变动画+自适应，客户端降级为纯色）
    html_mail = f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"UTF-8\"><title>信息化监控告警</title>
<style>
 body {{
   margin:0;padding:0;
   background:{gradient_css};
   background-size:400% 400%;
   animation:gradientMove 15s ease infinite;
   -webkit-font-smoothing:antialiased;
 }}
 @keyframes gradientMove {{
   0%   {{background-position:0% 50%;}}
   50%  {{background-position:100% 50%;}}
   100% {{background-position:0% 50%;}}
 }}
</style>
<!--[if mso]>
<style>
body, .wrapper {{ background:{fallback_color} !important; }}
</style>
<![endif]-->
<style>
 .wrapper {{ padding:40px 0;width:100%;box-sizing:border-box; }}
 .main {{
   width:100%;max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;
   box-shadow:0 6px 18px rgba(0,0,0,.08);
   padding:28px 34px;font-size:14px;color:#333;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;
 }}
 .row {{ margin:10px 0; }}
 code {{ background:#f2f4f8;padding:2px 4px;border-radius:4px;font-size:13px; }}
 ul {{ margin:4px 0 12px 18px;padding-left:0; }}
</style>
</head>
<body>
  <div class=\"wrapper\">
     <div class=\"main\">"""

    # 图片放在正文顶部（如有）
    if image_path:
        html_mail += """
       <div class=\"row\"><img src=\"cid:alert_chart\" style=\"width:100%;max-width:600px;border-radius:10px;\" /></div>"""

    html_mail += f"""
       <div class=\"row\"><strong>告警级别:</strong> S{one['severity']} {"恢复" if one['is_recover'] else "告警"}</div>
      <div class=\"row\"><strong>告警名称:</strong> {one['title']}</div>
      <div class=\"row\"><strong>{'集群分组' if one.get('is_origin_prometheus', False) else '业务分组'}:</strong> {one['group']}</div>
      <div class=\"row\"><strong>主机地址:</strong> <code>{host_line}</code></div>"""
    if host_name_line:
        html_mail += f"""
      <div class=\"row\"><strong>主机名称:</strong> <code>{host_name_line}</code></div>"""
    if event_id_line:
        html_mail += f"""
      <div class=\"row\"><strong>事件ID:</strong> <code>{event_id_line}</code></div>"""
    if one['is_recover']:
        # 恢复事件：先显示触发时间，再显示恢复时间
        html_mail += f"""
      <div class=\"row\"><strong>触发时间:</strong> {one.get('trigger_time', 'N/A')}</div>
      <div class=\"row\"><strong>恢复时间:</strong> {one['time']}</div>"""
    else:
        html_mail += f"""
      <div class=\"row\"><strong>触发时间:</strong> {one['time']}</div>"""
    html_mail += f"""
      <div class=\"row\"><strong>发送时间:</strong> {send_time}</div>
      <div class=\"row\"><strong>告警描述:</strong></div>
      <ul>
        {''.join(f'<li>{n}</li>' for n in notes)}
      </ul>
     </div>
   </div>
 </body>
 </html>"""

    # 调试模式保存 HTML 到本地
    if DEBUG_MODE:
        fname = f"/tmp/{'recover' if one['is_recover'] else 'alert'}_{one['title']}.html"
        with open(fname, "w", encoding="utf-8") as fp:
            fp.write(html_mail)
        logger.info("📝 本地预览文件: %s", fname)

    # 无图片：保持原逻辑
    if not image_path:
        msg = MIMEText(html_mail, "html", "utf-8")
        msg["From"] = EMAIL_FROM
        msg["To"] = ",".join(recipients)
        msg["Subject"] = Header(subject, "utf-8")
        return msg

    # 有图片：multipart/related + cid
    root = MIMEMultipart('related')
    root["From"] = EMAIL_FROM
    root["To"] = ",".join(recipients)
    root["Subject"] = Header(subject, "utf-8")

    alt = MIMEMultipart('alternative')
    root.attach(alt)
    alt.attach(MIMEText(html_mail, "html", "utf-8"))

    try:
        with open(image_path, 'rb') as fp:
            img = MIMEImage(fp.read(), _subtype='png')
        img.add_header('Content-ID', '<alert_chart>')
        img.add_header('Content-Disposition', 'inline', filename='alert_chart.png')
        root.attach(img)
    except Exception as e:
        logger.error("加载邮件图片失败: %s", e)

    return root


def send_email(msg: MIMEText, recipients: list[str]) -> None:
    """SMTP 发送"""
    if not EMAIL_PASS:
        logger.error("❌ MAIL_PASS 未配置，无法发送邮件")
        return
    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.sendmail(EMAIL_FROM, recipients, msg.as_string())
        logger.info("✅ 邮件发送 → %s", recipients)
    except Exception as e:
        logger.error("❌ 邮件发送失败: %s", e)
        logger.debug(traceback.format_exc())


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main() -> None:
    payload = load_payload()
    
    # 应用 params 覆盖（params > 环境变量 > 默认值）
    params = payload.get("params", {})
    apply_params_override(params)
    
    items   = aggregate_events(payload)

    recipients = [x for x in (payload.get("sendtos") or []) if isinstance(x, str) and "@" in x]
    if not recipients:
        logger.warning("⚠️ 无有效收件人，终止发送")
        return
    logger.debug("📧 收件人: %s", recipients)

    # 邮件按条发送；聚合告警/恢复告警跳过图片
    for want_recover in (False, True):
        for item in items:
            if item["is_recover"] != want_recover:
                continue

            image_path = ""
            try:
                image_path = generate_email_alert_image(payload)
            except Exception:
                logger.error("生成邮件告警图片异常", exc_info=True)
                image_path = ""

            send_email(build_email(item, recipients, image_path=image_path), recipients)

            if image_path:
                try:
                    os.remove(image_path)
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("❌ 脚本异常: %s", exc)
        logger.debug(traceback.format_exc())
        sys.exit(1)
