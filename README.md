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

## ⚙️ 前置要求（Fork 本仓库必读）

如果你 Fork 了本仓库并希望使用自动同步功能，**必须先配置 `CI_TOKEN` Secret**，否则子模块同步工作流会失败。

### 配置步骤

#### 1️⃣ 生成 Personal Access Token (PAT)

**Classic Token（推荐）**：
1. GitHub 头像 → `Settings` → `Developer settings` → `Personal access tokens` → `Tokens (classic)`
2. `Generate new token (classic)`
3. 勾选权限：
   - ✅ `repo`（包含 public_repo）
   - ✅ `workflow`
4. 复制生成的 token（只显示一次）

**Fine-grained Token**：
1. GitHub 头像 → `Settings` → `Developer settings` → `Personal access tokens` → `Fine-grained tokens`
2. `Generate new token`
3. Repository access：选择本仓库
4. 设置权限：
   - ✅ `Contents: Read and write`
   - ✅ `Actions: Read and write`
5. 复制生成的 token

#### 2️⃣ 添加到仓库 Secrets

1. 进入你的仓库 → `Settings` → `Secrets and variables` → `Actions`
2. 点击 `New repository secret`
3. Name: `CI_TOKEN`
4. Value: 粘贴刚才复制的 token
5. `Add secret`

#### 3️⃣ 验证

配置完成后，可以手动触发 `submodules-sync.yml` 工作流测试：
- `Actions` → `子模块自动同步` → `Run workflow`

> 💡 **为什么需要 CI_TOKEN？**
> 1. `GITHUB_TOKEN` 权限不足以推送到受保护分支
> 2. `GITHUB_TOKEN` 触发的 `repository_dispatch` 不会启动下游工作流（GitHub 防循环机制）

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
├── Dockerfile                         # 多阶段构建（CGO_ENABLED=0 静态链接）
├── docker-entrypoint.sh               # 容器启动脚本
├── .github/workflows/
│   ├── submodules-sync.yml            # 子模块 tag 对齐同步
│   └── docker-publish.yml             # 自动构建工作流
└── README.md
```

### 本地构建

```bash
# 克隆仓库（包含子模块）
git clone --recurse-submodules https://github.com/iflyelf/nightingale-docker.git
cd nightingale-docker

# 查看子模块当前锁定的版本
cd upstream && git describe --tags --exact-match

# 构建镜像（自动静态链接编译）
docker build -t nightingale:local .

# 运行测试
docker run --rm nightingale:local n9e --version
```

### 编译特性

本项目使用 **CGO_ENABLED=0** 进行纯静态链接编译：

- ✅ 生成完全静态的二进制文件，无外部动态库依赖
- ✅ 支持 linux/amd64、linux/arm64 交叉编译
- ✅ 镜像可在任意 Linux 发行版运行，无 glibc 版本限制
- ✅ 更小的二进制体积，更快的启动速度

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
# 检出到指定 tag（保持版本对齐）
git checkout v8.2.0
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

本项目采用**两段式**工作流，确保子模块始终对齐到上游的稳定 **tag**（而非漂移的 `main` 分支 HEAD）：

#### 1️⃣ 子模块同步 `submodules-sync.yml`

参考 [node_exporter 的同步方案](https://github.com/danxiaonuo/node_exporter/blob/main/.github/workflows/submodules-sync.yml) 实现：

1. **定时触发**：每 6 小时检查一次上游新版本
2. **获取最新 tag**：`git tag --sort=-version:refname` 取最新语义化版本
3. **版本对比**：仅当上游 tag 高于当前锁定 tag 时才更新
4. **checkout 到 tag**：`git checkout <tag>` 精确对齐版本（而非 `--remote --merge` 拉取漂移的 HEAD）
5. **提交并触发构建**：提交子模块指针变更，通过 `repository_dispatch` 触发镜像构建

> 💡 **为什么不用 `git submodule update --remote --merge`？**
> 该命令会把子模块拉到上游默认分支的最新 commit，可能落在两个 tag 之间的开发中间态，导致构建的镜像版本无法与官方 release tag 对齐。改用 `git checkout <tag>` 后，子模块指针始终落在明确的发布版本上。

> ⚠️ **需要配置 `CI_TOKEN` Secret**（一个具有 `repo` + `workflow` 权限的 PAT）：
> 1. 分支保护可能拒绝默认 `GITHUB_TOKEN` 推送提交；
> 2. 使用默认 `GITHUB_TOKEN` 发出的 `repository_dispatch` 事件**不会**触发下游 `docker-publish.yml`（GitHub 的防循环机制）。
>
> **Classic PAT** 权限：`repo`（含 public_repo）+ `workflow`
>
> **Fine-grained PAT** 权限：`Contents: Read & Write` + `Actions: Read & Write`
>
> 生成后在仓库 `Settings → Secrets and variables → Actions → New repository secret` 中添加，名称为 `CI_TOKEN`。

#### 2️⃣ 镜像构建 `docker-publish.yml`

1. **触发方式**：子模块同步完成后自动触发 / 每周一定时兜底 / 手动触发
2. **读取锁定 tag**：`git describe --tags --exact-match` 读取对齐的版本
3. **应用补丁**：自动执行 Python 补丁脚本
4. **多架构构建**：linux/amd64、linux/arm64（CGO_ENABLED=0 静态链接）
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
