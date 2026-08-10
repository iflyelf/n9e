# Gateway API 使用指南

本文档说明如何在 Nightingale Helm Chart 中使用 Gateway API 替代传统 Ingress 暴露服务。

> **默认关闭**：Chart 默认不启用 Gateway API（`gateway.enabled: false`），默认部署不会创建任何 Gateway/HTTPRoute 资源。仅当需要对外暴露 Web 界面时，按下文说明显式启用。

## 什么是 Gateway API？

Gateway API 是 Kubernetes 官方推出的下一代流量管理 API，作为 Ingress 的现代化替代方案：

- **表达力更强**：原生支持 header 路由、权重分流、流量镜像等高级功能
- **角色分离**：GatewayClass（集群管理员）、Gateway（运维）、Route（开发）三层设计
- **可移植性好**：标准化 API，不依赖厂商特定注解
- **协议支持广**：HTTP/HTTPS/TLS/TCP/UDP/gRPC

官方文档：https://gateway-api.sigs.k8s.io/

## 前置要求

### 1. 安装 Gateway API CRD

```bash
# 安装标准 CRD（包含 GatewayClass、Gateway、HTTPRoute 等）
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

# 验证安装
kubectl get crd | grep gateway.networking.k8s.io
```

预期输出：
```
gatewayclasses.gateway.networking.k8s.io
gateways.gateway.networking.k8s.io
httproutes.gateway.networking.k8s.io
referencegrants.gateway.networking.k8s.io
```

### 2. 安装支持的网关控制器

Gateway API 需要一个控制器来实际处理流量。以下是常见选择：

#### 方案 1：nginx-gateway-fabric（推荐新手）

```bash
# 安装
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/crds.yaml
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/manifests/nginx-gateway.yaml

# 验证
kubectl get pods -n nginx-gateway
kubectl get gatewayclass
```

#### 方案 2：Istio（适合微服务网格）

```bash
istioctl install --set profile=minimal -y
kubectl get gatewayclass
```

#### 方案 3：Envoy Gateway

```bash
helm install eg oci://docker.io/envoyproxy/gateway-helm --version v1.2.4 -n envoy-gateway-system --create-namespace
kubectl get gatewayclass
```

#### 方案 4：Cilium（CNI 集成）

```bash
cilium install --set kubeProxyReplacement=true
cilium hubble enable
kubectl get gatewayclass
```

更多实现参考：https://gateway-api.sigs.k8s.io/implementations/

### 3. 确认 GatewayClass 可用

```bash
kubectl get gatewayclass
```

记住输出的 GatewayClass 名称（如 `nginx`、`istio`、`cilium`），后续需要配置到 `gateway.gatewayClassName`。

## 部署场景

### 场景 1：独立 Gateway（推荐）

Chart 自己创建独立的 Gateway 和 HTTPRoute，适合 n9e 独占一个网关的场景。

**values.yaml 配置：**

```yaml
gateway:
  enabled: true
  create: true
  gatewayClassName: "nginx"  # 改为你的 GatewayClass 名称
  listeners:
    - name: http
      port: 80
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: Same
    # HTTPS（需预先创建 TLS Secret）
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
    rules:
      - matches:
          - path:
              type: PathPrefix
              value: /
```

**安装命令：**

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.gatewayClassName=nginx \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

**验证：**

```bash
# 查看资源
kubectl get gateway,httproute -n n9e

# 查看 Gateway 状态（等待 Programmed=True）
kubectl describe gateway -n n9e

# 获取 Gateway 外部地址
kubectl get gateway -n n9e -o jsonpath='{.items[0].status.addresses[0].value}'

# 测试访问（假设外部地址为 192.168.1.100）
curl -H "Host: n9e.example.com" http://192.168.1.100
```

### 场景 2：引用共享 Gateway

大规模集群通常由运维统一管理一个共享 Gateway，应用只需创建 HTTPRoute 路由到这个 Gateway。

**假设共享 Gateway 已存在：**

```bash
# 查看集群已有 Gateway
kubectl get gateway -A

# 示例输出
NAMESPACE         NAME             CLASS   ADDRESS
gateway-system    shared-gateway   nginx   203.0.113.10
```

**values.yaml 配置：**

```yaml
gateway:
  enabled: true
  create: false  # 不创建独立 Gateway
  httpRoute:
    create: true
    # 显式引用外部 Gateway
    parentRefs:
      - name: shared-gateway
        namespace: gateway-system
        sectionName: http  # 引用 Gateway 的 http 监听器
    hostnames:
      - n9e.example.com
    rules:
      - matches:
          - path:
              type: PathPrefix
              value: /
```

**安装命令：**

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.create=false \
  --set gateway.httpRoute.parentRefs[0].name=shared-gateway \
  --set gateway.httpRoute.parentRefs[0].namespace=gateway-system \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

**注意事项：**

1. 如果 Gateway 和 HTTPRoute 在不同命名空间，需创建 `ReferenceGrant` 授权跨命名空间引用：

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-n9e-to-gateway
  namespace: gateway-system
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: n9e
  to:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: shared-gateway
```

### 场景 3：多域名/路径规则

**需求：同时支持多个域名和不同路径前缀。**

```yaml
gateway:
  enabled: true
  httpRoute:
    hostnames:
      - n9e.example.com
      - monitor.example.com
    rules:
      # 规则 1：所有路径
      - matches:
          - path:
              type: PathPrefix
              value: /
      # 规则 2：特定路径用权重分流（金丝雀发布）
      - matches:
          - path:
              type: PathPrefix
              value: /api/v1/
        filters:
          - type: RequestHeaderModifier
            requestHeaderModifier:
              add:
                - name: X-Canary
                  value: "true"
        backendRefs:
          - name: n9e-center
            port: 17000
            weight: 90
          - name: n9e-center-canary
            port: 17000
            weight: 10
```

### 场景 4：HTTPS + 自动证书

结合 cert-manager 自动签发 TLS 证书。

**前置：安装 cert-manager**

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml
```

**创建 ClusterIssuer（Let's Encrypt）：**

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@example.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - http01:
          gatewayHTTPRoute:
            parentRefs:
              - name: n9e-gateway
                namespace: n9e
                kind: Gateway
```

**创建 Certificate：**

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: n9e-tls
  namespace: n9e
spec:
  secretName: n9e-tls
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - n9e.example.com
```

**values.yaml 配置 HTTPS 监听器：**

```yaml
gateway:
  enabled: true
  listeners:
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
    hostnames:
      - n9e.example.com
```

### 场景 5：Cilium 网络模式（亲和性/调度说明）

如果集群使用 Cilium 作为 CNI 和 Gateway API 控制器，调度模型与其他控制器**明显不同**，需要特别理解。

#### Cilium 的数据面架构

- 创建 Gateway 后，cilium-operator 会自动生成一个 `LoadBalancer` 类型的 Service 和 `CiliumEnvoyConfig`
- **实际处理流量的 Envoy 代理是 Cilium 的 DaemonSet**（`cilium-envoy`，或嵌入在 `cilium-agent` 中），**每个节点运行一个实例**
- 因为是 DaemonSet，天然分布在所有节点上，传统的「把副本打散」的 `podAntiAffinity` **不适用**

#### 亲和性该在哪里配？

| 需求 | 配置位置 | 说明 |
|------|---------|------|
| 控制 Envoy 代理跑在哪些节点 | **Cilium 自身的 Helm values** | 不在本 chart 内 |
| Gateway 对外 IP / LB 池选择 | 本 chart `gateway.addresses` / `gateway.infrastructure.annotations` | 标准字段 |

**⚠️ 重要：标准 Gateway 资源没有 `affinity` 字段。** Envoy 数据面的调度亲和性需要在**安装 Cilium 时**配置：

```yaml
# 这是 Cilium 的 values.yaml（helm install cilium 时使用），不是 n9e-chart
envoy:
  # 是否启用独立的 cilium-envoy DaemonSet
  enabled: true
  # Envoy DaemonSet 的节点选择器
  nodeSelector:
    ingress-ready: "true"
  # Envoy DaemonSet 的亲和性
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/gateway
                operator: Exists
  # 污点容忍
  tolerations:
    - key: node-role.kubernetes.io/gateway
      operator: Exists
```

修改后需重启 Cilium：`kubectl rollout restart daemonset cilium-envoy -n kube-system`

#### 本 chart 中 Cilium 相关配置

```yaml
gateway:
  enabled: true
  gatewayClassName: "cilium"
  # 指定对外 IP（需先配置 CiliumLoadBalancerIPPool）
  addresses:
    - type: IPAddress
      value: "192.168.1.100"
  infrastructure:
    # 传递到自动生成的 LoadBalancer Service 的注解
    annotations:
      # 从指定 LB IP 池分配地址
      io.cilium/lb-ipam-ips: "192.168.1.100"
  httpRoute:
    hostnames:
      - n9e.example.com
```

#### 前置：配置 Cilium LB-IPAM

使用 `gateway.addresses` 前，需先创建 IP 池：

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: n9e-pool
spec:
  blocks:
    - cidr: "192.168.1.0/24"
```

验证 Gateway 是否分配到 IP：

```bash
kubectl get gateway -n n9e
# ADDRESS 列应显示分配的 IP

kubectl get svc -n n9e -l io.cilium/gateway-name
# 查看自动生成的 LoadBalancer Service
```

## 故障排查

### 1. Gateway 状态为 Pending

```bash
kubectl describe gateway -n n9e
```

**常见原因：**
- GatewayClass 不存在或控制器未运行
- 云厂商 LoadBalancer 配额不足
- 监听器配置错误

**解决：**
```bash
# 检查 GatewayClass
kubectl get gatewayclass

# 检查控制器 Pod
kubectl get pods -n nginx-gateway  # 或对应命名空间

# 查看控制器日志
kubectl logs -n nginx-gateway <pod-name>
```

### 2. HTTPRoute 不生效

```bash
kubectl describe httproute -n n9e
```

**常见原因：**
- `parentRefs` 引用的 Gateway 不存在
- 跨命名空间引用缺少 `ReferenceGrant`
- `hostnames` 与实际请求 Host 不匹配
- `backendRefs` 的 Service 不存在

**解决：**
```bash
# 检查 parentRefs 是否正确
kubectl get gateway <gateway-name> -n <namespace>

# 检查后端 Service
kubectl get svc -n n9e

# 测试路由（使用 Gateway IP）
kubectl get gateway -n n9e -o jsonpath='{.items[0].status.addresses[0].value}'
curl -v -H "Host: n9e.example.com" http://<gateway-ip>
```

### 3. 跨命名空间引用失败

报错：`invalid cross namespace reference from n9e/n9e-center to gateway-system/shared-gateway`

**解决：创建 ReferenceGrant**

```bash
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-n9e-routes
  namespace: gateway-system
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: n9e
  to:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: shared-gateway
EOF
```

## 与 Ingress 对比

| 维度 | Ingress | Gateway API |
|------|---------|-------------|
| 标准化 | 核心功能标准，高级功能依赖注解 | 完全标准化 API |
| 表达力 | 基础 HTTP 路由 | HTTP/TCP/UDP/gRPC + 高级路由 |
| 角色分离 | 无 | GatewayClass/Gateway/Route 三层 |
| 多协议 | 仅 HTTP/HTTPS | 支持 TCP、UDP、TLS passthrough |
| 可移植性 | 差（注解各家不同） | 好（标准字段） |
| 成熟度 | 稳定（v1，2021 GA） | GA（v1.0，2023 GA） |
| 生态 | 广泛（nginx-ingress、traefik 等） | 快速增长（Istio、Cilium、Envoy Gateway） |

## 迁移建议

如果你当前使用 Ingress，可以平滑迁移到 Gateway API：

1. **保持 Ingress 运行**，先在测试环境部署 Gateway API
2. **验证功能完整**：确认所有路由、HTTPS、高级功能都正常
3. **灰度切流量**：DNS 或 LoadBalancer 权重逐步切换
4. **完全迁移后删除 Ingress**

大部分控制器都支持 Ingress 和 Gateway API 并存，可以逐步迁移应用。

## 参考资料

- [Gateway API 官方文档](https://gateway-api.sigs.k8s.io/)
- [Gateway API 实现列表](https://gateway-api.sigs.k8s.io/implementations/)
- [从 Ingress 迁移到 Gateway API](https://gateway-api.sigs.k8s.io/guides/migrating-from-ingress/)
- [nginx-gateway-fabric](https://github.com/nginxinc/nginx-gateway-fabric)
- [Istio Gateway API](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)

---

**有问题？**提交 Issue 或查看 [主 README](./README.md)。
