# Nightingale Helm Chart 部署指南

本文档提供 Nightingale (夜莺) 在 Kubernetes 集群的完整部署步骤。

## 快速部署（5 分钟上手）

### 前提条件

- Kubernetes 1.19+ 集群
- Helm 3.2.0+
- 已部署 MySQL 和 Redis（外置连接）
- **MySQL 已创建 `n9e_v6` 数据库**（n9e 会自动建表）

### 1. 准备数据库

```bash
# 如果使用 Percona MySQL Cluster,先创建数据库
kubectl exec -it -n mysql mysql-cluster-mysql-0 -c mysql -- \
  mysql -uroot -p'<密码>' -e "CREATE DATABASE IF NOT EXISTS n9e_v6 DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci"

# 获取 Percona 自动生成的密码
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo
```

### 2. 准备命名空间

```bash
kubectl create namespace n9e
```

### 3. 给节点打标签（重要）

Chart 默认在 `values.yaml` 中启用了**硬性**节点亲和性（`nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution`），要求 Pod 只能调度到带有 `application/n9e=nightingale` 标签的节点。**未打标签的集群会导致 Pod 一直处于 `Pending` 状态。**

```bash
# 查看节点
kubectl get nodes

# 给目标节点打标签（替换为实际节点名，可给多个节点打标签）
kubectl label node <node-name> application/n9e=nightingale

# 验证标签
kubectl get nodes -l application/n9e=nightingale
```

> 如果不想使用节点亲和性固定节点，可在部署时关闭：`--set affinity=null`，或在自定义 values 中将 `affinity: {}` 置空。

### 4. 部署中心端

```bash
# 进入 Chart 所在的父目录（包含 n9e-chart 目录的目录）

# 方式一：直接安装（使用 values.yaml 中的默认配置）
helm install n9e-center ./n9e-chart --namespace n9e

# 方式二：覆盖关键配置
helm install n9e-center ./n9e-chart --namespace n9e \
  --set externalDatabase.password="your-mysql-password" \
  --set externalRedis.password="your-redis-password"
```

### 5. 访问 Web UI

```bash
# 端口转发
kubectl port-forward -n n9e svc/n9e-center 17000:17000

# 浏览器访问
http://localhost:17000

# 默认账号
用户名: root
密码: root.2020
```

## 生产环境部署

### 0. Percona MySQL Cluster 集成说明

如果使用 Percona Operator 部署的 MySQL 集群,请注意以下要点:

#### 连接方式选择

Percona Operator 自动创建多个 Service 和端口:


| Service                       | 端口 | 用途            | 推荐场景                         |
| ----------------------------- | ---- | --------------- | -------------------------------- |
| `mysql-cluster-mysql-primary` | 3306 | 直连主节点      | **推荐**: 避免 Router 层权限问题 |
| `mysql-cluster-router`        | 6446 | Router 读写端口 | Group Replication 负载均衡       |
| `mysql-cluster-router`        | 6447 | Router 只读端口 | 只读查询                         |
| `mysql-cluster-router`        | 3306 | Router 标准端口 | ⚠️ 行为不确定,不推荐           |

#### 获取数据库密码

```bash
# Percona Operator 自动生成的 root 密码
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo

# 查看完整的连接配置 (包含推荐的 host/port)
kubectl get secret -n mysql mysql-cluster-psuser-root -o yaml
```

#### 数据库初始化

n9e 首次启动时会自动创建表结构,但需要提前创建数据库:

```bash
# 连接到 MySQL 主节点
kubectl exec -it -n mysql mysql-cluster-mysql-0 -c mysql -- \
  mysql -uroot -p'<从Secret获取的密码>'

# 在 MySQL 中执行
CREATE DATABASE IF NOT EXISTS n9e_v6 DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
SHOW DATABASES LIKE 'n9e_v6';
EXIT;
```

**注意**:

- Percona Group Replication 集群中,只有主节点 (primary) 可写
- 副本 (replica) 处于 `super_read_only` 模式
- 不确定哪个是主节点时,使用 `mysql-cluster-mysql-primary` Service (自动指向主节点)

### 1. 准备自定义配置文件

```bash
cp n9e-chart/values.yaml n9e-chart/values-production.yaml
```

### 2. 修改关键配置

编辑 `values-production.yaml`：

```yaml
# 副本数
replicaCount: 3

# 镜像
image:
  repository: iflyelf/nightingale
  tag: "v8.2.0-aggregation"
  pullPolicy: IfNotPresent

# 外置数据库
externalDatabase:
  type: mysql
  # Percona MySQL Cluster 推荐使用 primary 直连或 Router 的读写端口
  # 方式1: 直连 primary (推荐,避免 Router 层权限问题)
  host: "mysql-cluster-mysql-primary.mysql.svc.cluster.local"
  port: 3306
  # 方式2: 使用 Router 读写端口 (Group Replication 负载均衡)
  # host: "mysql-cluster-router.mysql.svc.cluster.local"
  # port: 6446  # 注意是 6446 而非 3306
  database: "n9e_v6"
  username: "root"
  password: "YOUR_MYSQL_PASSWORD"  # 修改为实际密码
  # 提示: Percona Operator 自动生成的密码在 Secret mysql-cluster-secrets 的 root 字段中
  # 获取: kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d

# 外置 Redis（Redis Cluster 6 节点分片集群）
externalRedis:
  type: cluster
  # cluster 模式列出所有节点，客户端自动发现拓扑（对应 redis-cluster 项目部署）
  address: "redis-cluster-0.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-1.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-2.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-3.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-4.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-5.redis-cluster-headless.redis.svc.cluster.local:6379"
  password: "YOUR_REDIS_PASSWORD"  # 修改为实际密码
  db: 0  # cluster 模式固定为 0

# 资源限制（按实际需求调整）
resources:
  requests:
    cpu: 4
    memory: 8Gi
  limits:
    cpu: 8
    memory: 16Gi

# 启用 HPA
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

# Gateway API 配置（以 Cilium 为例）
gateway:
  enabled: true
  create: true
  gatewayClassName: "cilium"
  # 指定对外 IP（需先配置 CiliumLoadBalancerIPPool）
  addresses:
    - type: IPAddress
      value: "192.168.1.100"
  infrastructure:
    # 传递到自动生成的 LoadBalancer Service 的注解
    annotations:
      io.cilium/lb-ipam-ips: "192.168.1.100"
  listeners:
    - name: http
      port: 80
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: Same
    # HTTPS 监听器（需预先创建 TLS Secret）
    - name: https
      port: 443
      protocol: HTTPS
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: n9e-tls
      allowedRoutes:
        namespaces:
          from: Same
  httpRoute:
    create: true
    hostnames:
      - n9e.example.com

# 注意：Envoy 数据面（cilium-envoy DaemonSet）的调度亲和性
#      需在安装 Cilium 的 Helm values 中配置（envoy.affinity），不在本 chart 内。
#      详见 GATEWAY.md 场景 5。
    rules:
      - matches:
          - path:
              type: PathPrefix
              value: /

# 节点亲和性（固定调度节点）
# 默认要求节点带 application/n9e=nightingale 标签，可按需修改标签键值
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: application/n9e
              operator: In
              values: ["nightingale"]
  # 多副本分散到不同节点，避免单点
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: app.kubernetes.io/name
              operator: In
              values: ["n9e"]
        topologyKey: kubernetes.io/hostname
```

> 部署前务必给目标节点打标签，否则硬性亲和性会导致 Pod `Pending`：
>
> ```bash
> kubectl label node <node-name> application/n9e=nightingale
> ```

### 3. 部署

```bash
helm install n9e-center ./n9e-chart \
  --namespace n9e \
  -f n9e-chart/values-production.yaml
```

### 4. 验证部署

```bash
# 查看 Pod 状态
kubectl get pods -n n9e -l app.kubernetes.io/instance=n9e-center

# 查看服务
kubectl get svc -n n9e

# 查看 Gateway 和 HTTPRoute
kubectl get gateway,httproute -n n9e

# 检查日志
kubectl logs -n n9e -l app.kubernetes.io/instance=n9e-center -f
```

## 边缘告警引擎部署

### 1. 确认中心端已开启 APIForService

确保中心端配置中 `config.http.apiForService.enable=true`，可通过 helm upgrade 修改：

```bash
helm upgrade n9e-center ./n9e-chart -n n9e \
  --set config.http.apiForService.enable=true \
  --reuse-values
```

### 2. 部署边缘节点

```bash
# 使用预置的边缘配置
helm install n9e-edge ./n9e-chart \
  -f n9e-chart/values-edge.yaml \
  --namespace n9e \
  --set config.edge.centerApi.addrs[0]="http://n9e-center.n9e.svc.cluster.local:17000"
```

### 3. 多边缘节点部署

每个边缘节点使用不同的 engineName：

```bash
# 边缘节点 1
helm install n9e-edge1 ./n9e-chart -f n9e-chart/values-edge.yaml -n n9e \
  --set config.alert.heartbeat.engineName=edge1

# 边缘节点 2
helm install n9e-edge2 ./n9e-chart -f n9e-chart/values-edge.yaml -n n9e \
  --set config.alert.heartbeat.engineName=edge2 \
  --set externalRedis.address="redis-edge2.redis.svc.cluster.local:6379"
```

## 常用运维操作

### 升级

```bash
# 查看当前版本
helm list -n n9e

# 升级（修改配置后）
helm upgrade n9e-center ./n9e-chart -n n9e -f values-production.yaml

# 查看升级历史
helm history n9e-center -n n9e
```

### 回滚

```bash
# 回滚到上一版本
helm rollback n9e-center -n n9e

# 回滚到指定版本
helm rollback n9e-center 2 -n n9e
```

### 扩缩容

```bash
# 手动扩容
kubectl scale deployment -n n9e n9e-center --replicas=5

# 或通过 helm upgrade
helm upgrade n9e-center ./n9e-chart -n n9e --set replicaCount=5 --reuse-values
```

### 卸载

```bash
# 卸载（保留 PVC）
helm uninstall n9e-center -n n9e

# 完全清理（包括 PVC）
helm uninstall n9e-center -n n9e
kubectl delete pvc -n n9e -l app.kubernetes.io/instance=n9e-center
```

## 监控与告警

### 暴露 Metrics

n9e 自身监控指标默认已启用（`config.http.exposeMetrics=true`），暴露在 `/metrics` 端点。

### 配置 Prometheus 抓取

```yaml
# ServiceMonitor（需安装 Prometheus Operator）
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 30s
```

### 关键指标

- `n9e_alert_rule_count` - 告警规则数量
- `n9e_alert_event_total` - 告警事件总数
- `n9e_pushgw_samples_total` - 接收的时序样本数
- `n9e_http_request_duration_seconds` - HTTP 请求延迟

## 故障排查

### Pod 启动失败

```bash
# 查看事件
kubectl describe pod -n n9e <pod-name>

# 查看日志
kubectl logs -n n9e <pod-name>

# 检查配置
kubectl get secret -n n9e n9e-center -o jsonpath='{.data.config\.toml}' | base64 -d
```

### 无法连接数据库

#### 常见错误 1: `connection refused`

**现象**: `dial tcp <IP>:3306: connect: connection refused`

**原因**: Service 的 Endpoints 为空,或端口配置错误

**排查步骤**:

```bash
# 1. 检查 MySQL Pod 状态
kubectl get pods -n mysql

# 2. 检查 Service Endpoints (关键!)
kubectl get svc -n mysql mysql-cluster-router
kubectl get endpoints -n mysql mysql-cluster-router
# 如果 ENDPOINTS 列为 <none>,说明没有就绪的 Pod

# 3. 检查 Pod 就绪状态
kubectl describe pod -n mysql <mysql-pod-name>

# 4. 查看 MySQL 日志
kubectl logs -n mysql <mysql-pod-name>
```

**解决方案**:

- 等待 MySQL Pod 完全启动 (初始化可能需要几分钟)
- 如果 Pod CrashLoop,检查存储/资源配置
- 确认 Service selector 与 Pod labels 匹配

#### 常见错误 2: `Access denied for user 'root'@'<IP>'`

**现象**: `ERROR 1045 (28000): Access denied for user 'root'@'172.16.x.x' (using password: YES)`

**原因**:

1. 密码不正确 (大小写敏感)
2. root 用户未开启远程访问权限
3. 通过 Router 连接时,MySQL 看到的来源 IP 不是 localhost

**排查步骤**:

```bash
# 1. 获取正确的密码 (Percona Operator 自动生成)
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo

# 2. 查看 Percona 推荐的连接配置
kubectl get secret -n mysql mysql-cluster-psuser-root -o yaml

# 3. 测试直连 primary
kubectl run mysql-client --rm -it --image=mysql:8.0 --restart=Never -n mysql -- \
  mysql -h mysql-cluster-mysql-primary.mysql.svc.cluster.local -P 3306 -uroot -p'<密码>' -e "SELECT 1"

# 4. 测试 Router 读写端口 (6446)
kubectl run mysql-client --rm -it --image=mysql:8.0 --restart=Never -n mysql -- \
  mysql -h mysql-cluster-router.mysql.svc.cluster.local -P 6446 -uroot -p'<密码>' -e "SELECT 1"
```

**解决方案**:

```yaml
# 方案1: 使用 primary 直连 (推荐)
externalDatabase:
  host: "mysql-cluster-mysql-primary.mysql.svc.cluster.local"
  port: 3306
  password: "从 Secret 获取的正确密码"

# 方案2: 使用 Router 的读写端口
externalDatabase:
  host: "mysql-cluster-router.mysql.svc.cluster.local"
  port: 6446  # 读写端口,不是 3306
  password: "从 Secret 获取的正确密码"
```

#### 常见错误 3: `The MySQL server is running with the --super-read-only option`

**现象**: 无法执行 CREATE/INSERT 等写操作

**原因**: 连接到了只读副本 (replica) 或 Router 的只读端口

**解决方案**:

- 确保使用 `mysql-cluster-mysql-primary` (primary) 或 Router 的 `6446` 端口
- 避免直接连 `mysql-cluster-mysql-0/1/2` (可能是副本)
- Router 的 `6447` 是只读端口,`6446` 才是读写端口

#### 测试数据库连通性

```bash
# 从 n9e Pod 内部测试
kubectl exec -n n9e <n9e-pod-name> -- \
  nc -zv mysql-cluster-mysql-primary.mysql.svc.cluster.local 3306

# 检查网络策略
kubectl get networkpolicies -n n9e
kubectl get networkpolicies -n mysql
```

### HPA 不生效

```bash
# 检查 Metrics Server
kubectl get deployment metrics-server -n kube-system

# 查看 HPA 状态
kubectl get hpa -n n9e

# 查看详细信息
kubectl describe hpa -n n9e n9e-center
```

## 性能调优

### 数据库连接池

```yaml
externalDatabase:
  maxOpenConns: 200  # 根据并发量调整
  maxIdleConns: 100
  maxLifetime: 7200
```

### Redis 超时

```yaml
externalRedis:
  dialTimeoutMills: 5000
  readTimeoutMills: 3000
  writeTimeoutMills: 3000
```

### 资源限制建议


| 场景                    | CPU  | 内存 |
| ----------------------- | ---- | ---- |
| 小规模（< 1000 台机器） | 2 核 | 4Gi  |
| 中规模（1000-5000 台）  | 4 核 | 8Gi  |
| 大规模（> 5000 台）     | 8 核 | 16Gi |

## 安全加固

### 启用 BasicAuth（Agent 接入）

```yaml
config:
  http:
    apiForAgent:
      basicAuth:
        agent001: "your-hashed-password"
```

### 启用 HTTPS

```yaml
config:
  http:
    certFile: "/etc/n9e/tls/tls.crt"
    keyFile: "/etc/n9e/tls/tls.key"

extraVolumes:
  - name: tls
    secret:
      secretName: n9e-tls-cert

extraVolumeMounts:
  - name: tls
    mountPath: /etc/n9e/tls
    readOnly: true
```

### 网络策略

```yaml
networkPolicy:
  enabled: true
  ingress:
    - from:
      - namespaceSelector:
          matchLabels:
            name: monitoring
      ports:
      - protocol: TCP
        port: 17000
```

## 告警通知脚本（邮件/飞书/IM/短信）

Nightingale 通过 stdin 管道调用 Python 通知脚本发送告警。本 Chart 提供 5 个内置脚本，
以「零改 Chart 模板」的方式挂载进 n9e 主容器运行。


| 脚本                            | 用途                                            |
| ------------------------------- | ----------------------------------------------- |
| `send_nightingale_email.py`     | 邮件通知（含 Grafana 图表渲染）                 |
| `send_nightingale_feishu.py`    | 飞书群机器人 webhook 通知                       |
| `send_nightingale_im.py`        | 飞书应用消息卡片（含告警屏蔽、AI 分析、协同群） |
| `send_nightingale_im_urgent.py` | 飞书电话加急通知                                |
| `send_nightingale_sms.py`       | 短信通知                                        |

### 架构说明

这些脚本必须在 n9e 主容器内可执行（n9e 进程 fork/exec 子进程并通过 stdin 喂入告警 JSON），
因此不能用独立 sidecar 容器运行。方案采用：

- **脚本分发**：ConfigMap `n9e-scripts` 挂载到 `/opt/n9e-scripts`
- **依赖安装**：initContainer 用 `pip install --target=/deps requests`，主容器通过 `PYTHONPATH=/deps` 引用
- **敏感配置**：Secret `n9e-scripts-secret` 通过环境变量注入
- **日志目录**：emptyDir 挂载到 `/data/n9e/alerts`（生产建议改用 PVC）

> 前提：n9e 主镜像已内置 `python3`（`iflyelf/nightingale:latest-aggregation` 已确认包含）。
> 脚本仅依赖 `requests`，其余均为 Python 标准库。

### 配置优先级

脚本配置支持三级覆盖，优先级从高到低：

```
payload.params（n9e 告警渠道下发）> 环境变量（Secret 注入）> 脚本内默认值
```

即告警渠道通过 params 下发的配置（如 `callback_server_url`、`feishu_domain`、
`grafana_token` 等）优先级最高，可覆盖容器环境变量。

### 部署步骤

**1. 修改敏感配置**

编辑 `manifests/n9e-scripts-secret.yaml`，填入实际的邮箱密码、Grafana Token、
飞书域名、回调服务地址等。集群内回调地址建议使用 Service 名称：

```yaml
CALLBACK_SERVER_URL: "http://n9e-gateway.n9e.svc.cluster.local:5000"
```

**2. 如需修改脚本，重新生成 ConfigMap**

脚本源文件位于 `scripts/` 目录。修改后运行生成命令：

```bash
python3 gen-scripts-configmap.py
```

**3. 应用 ConfigMap 和 Secret**

```bash
kubectl apply -f manifests/n9e-scripts-configmap.yaml
kubectl apply -f manifests/n9e-scripts-secret.yaml
```

**4. 部署 Chart（附加 values 片段）**

`-f` 的 values 文件路径需带上 chart 目录前缀（相对当前执行目录）：

```bash
# 在 n9e-chart 的上级目录执行
helm upgrade --install n9e-center ./n9e-chart -n n9e \
  -f ./n9e-chart/values.yaml \
  -f ./n9e-chart/values-notify-scripts.yaml
```

或先进入 chart 目录，用 `.` 指向当前目录：

```bash
cd n9e-chart
helm upgrade --install n9e-center . -n n9e \
  -f values.yaml \
  -f values-notify-scripts.yaml
```

**5. 在 Nightingale 后台配置通知媒介**

脚本路径填写容器内绝对路径：

```
/opt/n9e-scripts/send_nightingale_email.py
/opt/n9e-scripts/send_nightingale_feishu.py
/opt/n9e-scripts/send_nightingale_im.py
/opt/n9e-scripts/send_nightingale_im_urgent.py
/opt/n9e-scripts/send_nightingale_sms.py
```

### 支持的环境变量


| 环境变量                                                  | 说明                      | 涉及脚本                |
| --------------------------------------------------------- | ------------------------- | ----------------------- |
| `LOG_DIR`                                                 | 日志目录                  | 全部                    |
| `IMG_DIR`                                                 | 临时图片目录              | im                      |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USER` / `EMAIL_FROM` | SMTP 服务器配置           | email                   |
| `MAIL_PASS`                                               | 邮箱密码                  | email                   |
| `GRAFANA_BASE_URL` / `GRAFANA_TOKEN`                      | Grafana 地址和 Token      | email / im / im_urgent  |
| `GRAFANA_DASHBOARD_UID` / `GRAFANA_DATASOURCE_UID`        | Grafana 仪表盘/数据源 UID | email / im / im_urgent  |
| `FEISHU_DOMAIN`                                           | 飞书开放平台域名          | feishu / im / im_urgent |
| `CALLBACK_SERVER_URL`                                     | 回调服务地址              | feishu / im / im_urgent |
| `DEFAULT_DOMAIN_URL`                                      | N9E 告警详情地址          | feishu / im / im_urgent |
| `SMS_GATEWAY_URL` / `SMS_TEMPLATE_ID` / `SMS_TYPE`        | 短信网关配置              | sms                     |

### 故障排查

**脚本执行提示 `timeout and killed process`（最常见）**

n9e 的通知脚本超时**不在配置文件或 Helm values 中**，而是在 Web 界面的
「通知媒介（Script 类型）」设置里，以毫秒为单位，**默认仅 5000ms（5 秒）**。
`send_nightingale_im.py` 需要串行完成「获取飞书 token → 创建临时 Dashboard →
Grafana 渲染图表 → 上传图片 → 发送卡片」，正常耗时就会超过 5 秒，因此被强制 kill。

**解决方法**（在 n9e Web 界面操作，配置存于数据库，无需改 Chart）：

1. 进入 `告警管理 → 通知媒介`，找到对应的 Script 媒介
2. 将「超时时间」从 `5000` 调大到 `30000`（即 30 秒），保存
3. 若仍偶发超时，可继续调大，或在 Secret 中压缩脚本内部超时（见下方环境变量）

脚本内部各阶段超时**全部环境变量化**，可通过 Secret 微调（默认值已优化）：

| 环境变量 | 默认值 | 说明 | 涉及脚本 |
|---------|--------|------|---------|
| `GRAFANA_RENDER_TIMEOUT` | `15`（秒） | Grafana 图片渲染 / 飞书图片上传 | email / im / im_urgent |
| `GRAFANA_API_TIMEOUT` | `8`（秒） | Grafana 仪表盘增删改查 | email / im / im_urgent |
| `FEISHU_API_TIMEOUT` | `6`（秒） | 飞书 token / 发送消息 / 加急 | im / im_urgent / feishu |
| `FEISHU_WEBHOOK_TIMEOUT` | `8`（秒） | 飞书群机器人 webhook 推送 | feishu |
| `CALLBACK_API_TIMEOUT` | `6`（秒） | 回调服务注册（屏蔽/AI分析/协同群） | im / im_urgent / feishu |
| `SMTP_TIMEOUT` | `10`（秒） | SMTP 邮件发送 | email |
| `SMS_API_TIMEOUT` | `10`（秒） | 短信网关请求 | sms |

> 建议：n9e Script 媒介超时（30s）应大于脚本内部各阶段超时之和，给脚本留出完整执行时间。

**脚本报 `ModuleNotFoundError: No module named 'requests'`**

- 检查 initContainer 是否成功执行：`kubectl logs <pod> -c install-python-deps -n n9e`
- 确认主容器 `PYTHONPATH=/deps` 已设置

**脚本无日志输出**

- 确认 `/data/n9e/alerts` 目录可写（emptyDir 或 PVC 已挂载）
- 查看 n9e 主进程日志中的脚本执行错误

**Grafana 图片渲染失败**

- 确认 Pod 网络可访问 Grafana，且 Grafana 已安装 image-renderer 插件
- 检查 `GRAFANA_TOKEN` 是否有效

## 参考文档

- [完整配置说明](./README.md)
- [Nightingale 官方文档](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/)
- [配置文件详解](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/install/configuration/)

---

**有问题？** 查看 [故障排查](./README.md#故障排查) 或提交 Issue。
