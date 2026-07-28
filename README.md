# Nightingale Docker 事件聚合版 🐳

[![Docker Pulls](https://img.shields.io/docker/pulls/iflyelf/nightingale?style=flat-square)](https://hub.docker.com/r/iflyelf/nightingale)
[![Docker Stars](https://img.shields.io/docker/stars/iflyelf/nightingale?style=flat-square)](https://hub.docker.com/r/iflyelf/nightingale)
[![Build Status](https://img.shields.io/github/actions/workflow/status/iflyelf/nightingale-docker/docker-publish.yml?style=flat-square)](https://github.com/iflyelf/nightingale-docker/actions)
[![License](https://img.shields.io/github/license/ccfos/nightingale?style=flat-square)](LICENSE)

> 基于 [ccfos/nightingale](https://github.com/ccfos/nightingale) 官方最新版本，集成事件聚合功能的 Docker 镜像

---

## 📌 项目说明

本项目通过 Git 子模块引入上游 Nightingale 代码，在构建时自动应用事件聚合补丁，打包为 Docker 镜像。

### 核心特性

✅ **自动同步上游**：每周一自动拉取最新代码并构建  
✅ **事件聚合**：60 秒聚合窗口，减少重复告警通知  
✅ **多架构支持**：linux/amd64、linux/arm64  
✅ **补丁脚本化**：Python 脚本自动应用聚合功能  
✅ **开箱即用**：预配置健康检查和启动脚本  

---

## 🚀 快速开始

### 1. 拉取镜像

```bash
docker pull iflyelf/nightingale:latest-aggregation
```

### 2. 启动容器

#### 基础运行

```bash
docker run -d --name n9e \
  -p 19000:19000 \
  -v /opt/n9e/etc:/opt/nightingale/etc \
  -v /opt/n9e/logs:/opt/nightingale/logs \
  iflyelf/nightingale:latest-aggregation
```

#### 完整配置（docker-compose）

```yaml
version: '3.8'

services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root123
      MYSQL_DATABASE: n9e_v6
    volumes:
      - mysql-data:/var/lib/mysql
    ports:
      - "3306:3306"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  nightingale:
    image: iflyelf/nightingale:latest-aggregation
    depends_on:
      - mysql
      - redis
    ports:
      - "19000:19000"
    volumes:
      - ./etc:/opt/nightingale/etc
      - ./logs:/opt/nightingale/logs
    environment:
      - TZ=Asia/Shanghai
      - WAIT_FOR=mysql:3306 redis:6379
    restart: unless-stopped

volumes:
  mysql-data:
```

### 3. 访问 Web 界面

```bash
# 默认访问地址
http://localhost:19000

# 默认账户
用户名: root
密码: root.2020
```

---

## 🔧 构建说明

### 项目结构

```
nightingale-docker/
├── upstream/                          # Git 子模块（ccfos/nightingale）
├── apply-aggregation-patch.py         # Python 补丁脚本
├── Dockerfile                         # 多阶段构建
├── docker-entrypoint.sh               # 容器启动脚本
├── .github/workflows/
│   └── docker-publish.yml             # 自动构建工作流
└── README.md
```

### 本地构建

```bash
# 克隆仓库（包含子模块）
git clone --recurse-submodules https://github.com/iflyelf/nightingale-docker.git
cd nightingale-docker

# 更新子模块到最新版本
git submodule update --remote --merge

# 构建镜像
docker build -t nightingale:local .

# 运行测试
docker run --rm nightingale:local n9e --version
```

---

## 📦 镜像标签

| 标签 | 说明 | 更新频率 |
|------|------|----------|
| `latest-aggregation` | 最新稳定版（事件聚合） | 每周一自动构建 |
| `v6.x.x-aggregation` | 特定版本（事件聚合） | 跟随上游版本 |
| `latest` | 最新稳定版 | 同 latest-aggregation |

---

## 🛠️ 补丁说明

### 补丁内容

`apply-aggregation-patch.py` 脚本自动修改 `alert/dispatch/dispatch.go`：

1. **新增结构体**：`AggregationKey`、`AggregatedEvents`
2. **新增字段**：`Dispatch` 结构体添加 `aggrCache` 和 `aggrLock`
3. **修改逻辑**：替换 `SendByNotifyRule` 为 `AddToAggregation`
4. **新增方法**：`AddToAggregation()`、`StartAggregationSender()`

### 手动应用补丁

```bash
cd upstream
git checkout main
cd ..
python3 apply-aggregation-patch.py

# 验证
grep -n "AggregationKey\|AddToAggregation" upstream/alert/dispatch/dispatch.go
```

---

## ⚙️ 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TZ` | 时区 | `Asia/Shanghai` |
| `LANG` | 语言 | `zh_CN.UTF-8` |
| `WAIT_FOR` | 等待依赖服务（格式：`host:port host:port`） | - |

### 挂载目录

| 容器路径 | 说明 |
|----------|------|
| `/opt/nightingale/etc` | 配置文件目录 |
| `/opt/nightingale/logs` | 日志文件目录 |
| `/opt/nightingale/data` | 数据文件目录 |

### 端口

| 端口 | 说明 |
|------|------|
| `19000` | Web UI + API |
| `18000` | PushGateway (可选) |

---

## 🔄 自动更新机制

### GitHub Action 工作流

`.github/workflows/docker-publish.yml` 实现以下功能：

1. **定时触发**：每周一 UTC 02:00（北京时间 10:00）
2. **更新子模块**：自动拉取上游最新代码
3. **应用补丁**：自动执行 Python 补丁脚本
4. **多架构构建**：linux/amd64、linux/arm64
5. **推送镜像**：自动推送到 Docker Hub

### 手动触发

```bash
# 通过 GitHub UI
Actions → 构建并推送 Nightingale 镜像 → Run workflow

# 通过 gh CLI
gh workflow run docker-publish.yml --repo iflyelf/nightingale-docker
```

---

## 📊 事件聚合功能

### 工作原理

```
事件时间轴：
00:00  事件1 (CPU高告警, 通知规则A)  → 加入聚合缓存
00:15  事件2 (CPU高告警, 通知规则A)  → 追加到缓存
00:45  事件3 (CPU高告警, 通知规则A)  → 追加到缓存
01:00  定时检查 → 聚合时间 ≥ 60s → 批量发送 [事件1, 事件2, 事件3]
```

### 参数调整

修改 `apply-aggregation-patch.py` 中的参数：

```python
# 聚合窗口（默认 60 秒）
if now-aggr.FirstTime >= 60 && len(aggr.Events) > 0 {

# 检查周期（默认 10 秒）
time.Sleep(10 * time.Second)
```

重新构建镜像生效。

---

## 🐛 故障排查

### 查看日志

```bash
# 容器日志
docker logs -f n9e

# 应用日志
docker exec n9e tail -f /opt/nightingale/logs/server.log
```

### 验证补丁

```bash
# 进入容器
docker exec -it n9e bash

# 检查聚合功能
grep -c "AggregationKey\|StartAggregationSender" /build/nightingale/alert/dispatch/dispatch.go
```

### 健康检查

```bash
# 手动健康检查
curl http://localhost:19000/api/n9e/ping

# 查看容器健康状态
docker inspect --format='{{.State.Health.Status}}' n9e
```

---

## 🤝 贡献

### 贡献者

- **原始实现**：[@danxiaonuo](https://github.com/danxiaonuo/nightingale)
- **Docker 封装**：[@iflyelf](https://github.com/iflyelf)
- **上游项目**：[@ccfos](https://github.com/ccfos/nightingale)

### 参与贡献

```bash
# Fork 本仓库
git clone --recurse-submodules https://github.com/你的用户名/nightingale-docker.git

# 修改补丁脚本或 Dockerfile
git add .
git commit -m "feat: your changes"

# 推送并创建 PR
git push origin main
```

---

## 📜 开源协议

本项目继承 Nightingale 的开源协议：[Apache License 2.0](LICENSE)

---

## 🔗 相关链接

- [Nightingale 官网](https://n9e.github.io/)
- [上游仓库](https://github.com/ccfos/nightingale)
- [Docker Hub](https://hub.docker.com/r/iflyelf/nightingale)
- [功能来源](https://github.com/danxiaonuo/nightingale)

---

**⭐ 如果这个项目对你有帮助，欢迎 Star 支持！**
