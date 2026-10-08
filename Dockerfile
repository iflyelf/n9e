#############################################################################
#  Nightingale 事件聚合版 多阶段构建
#  - builder(构建阶段) = iflyelf/ubuntu:latest
#      已预装 Go / Node / Python / 完整工具链与 PKG_DEPS, 无需再装庞大依赖列表
#      (构建更快、更稳), 仅编译 n9e / n9e-edge / n9e-pushgw 三个静态二进制。
#  - runtime(运行阶段) = iflyelf/ubuntu:lite
#      仅拷贝编译产物 + 最小运行依赖(bash/nc/curl/ca-certificates/tzdata), 镜像更小。
#############################################################################

####################################################################
#                 构建阶段 (builder) = ubuntu:latest              #
####################################################################
FROM iflyelf/ubuntu:latest AS builder

# 作者描述信息
LABEL org.opencontainers.image.authors="iflyelf" \
      org.opencontainers.image.vendor="iflyelf" \
      org.opencontainers.image.title="Nightingale with Event Aggregation" \
      org.opencontainers.image.description="Nightingale 监控系统 - 集成事件聚合功能"

ARG TARGETARCH
ARG TARGETVARIANT

# 时区设置
ARG TZ=Asia/Shanghai
ENV TZ=$TZ
# 语言设置
ARG LANG=zh_CN.UTF-8
ENV LANG=$LANG

# 环境设置
ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=$DEBIAN_FRONTEND

# GO 环境变量（builder 已预装 Go，此处仅配置代理与静态链接）
ARG GOPROXY=https://goproxy.cn,direct
ENV GOPROXY=$GOPROXY
ARG GOSUMDB=sum.golang.org
ENV GOSUMDB=$GOSUMDB
# 静态链接编译(禁用 CGO, 生成纯静态二进制, 支持交叉编译)
ARG CGO_ENABLED=0
ENV CGO_ENABLED=$CGO_ENABLED

# 固定 Go 工具链版本: iflyelf/ubuntu:latest 预装的 Go 可能较新(如 1.27),
# 会导致 x/net(http2) 与 grpc 的 API 不兼容(undefined: http2.TrailerPrefix),
# 这里通过 GOTOOLCHAIN 固定为 go1.26.4, 与历史构建环境保持一致。
ARG GO_TOOLCHAIN=go1.26.4
ENV GOTOOLCHAIN=$GO_TOOLCHAIN

# ***** 复制源码并应用事件聚合补丁 *****
COPY upstream /build/nightingale
COPY apply-aggregation-patch.py /build/

RUN set -eux && \
    cd /build && \
    python3 apply-aggregation-patch.py /build/nightingale/alert/dispatch/dispatch.go && \
    echo "✅ 事件聚合补丁应用完成"

# 上游 tag, 由构建参数传入 (源码经 COPY 后无 .git, Makefile 的 git describe 会失败)
ARG UPSTREAM_TAG=unknown
ENV UPSTREAM_TAG=$UPSTREAM_TAG

# ***** 编译 Nightingale *****
RUN set -eux && \
    cd /build/nightingale && \
    go mod download && \
    # statik 用于将前端文件嵌入二进制 (fe.sh 依赖), 必须先安装到 GOPATH/bin
    go install github.com/rakyll/statik@v0.1.7 && \
    # 下载前端静态文件 (n9e/fe release), fe.sh 会自动获取最新版前端并解压到 ./pub
    chmod +x fe.sh && \
    ./fe.sh && \
    # 先建好安装目录 (go build -o 要求父目录存在)
    mkdir -p /opt/nightingale/etc /opt/nightingale/logs /opt/nightingale/data && \
    # 直接调用 go build 传入版本号, 绕过 Makefile 中失败的 git describe
    RELEASE_VERSION="${UPSTREAM_TAG}" && \
    LDFLAGS="-w -s -X github.com/ccfos/nightingale/v6/pkg/version.Version=${RELEASE_VERSION}" && \
    go build -ldflags "$LDFLAGS" -o /opt/nightingale/n9e ./cmd/center/main.go && \
    go build -ldflags "$LDFLAGS" -o /opt/nightingale/n9e-edge ./cmd/edge/ && \
    go build -ldflags "$LDFLAGS" -o /opt/nightingale/n9e-pushgw ./cmd/pushgw/main.go && \
    ls -lh /opt/nightingale/n9e* && \
    # 安装配置文件与前端静态目录
    cp -r etc/* /opt/nightingale/etc/ && \
    # pub 前端目录存在才复制 (最新版可能使用 embed 方式内嵌)
    if [ -d pub ]; then cp -r pub /opt/nightingale/pub; fi && \
    echo "✅ Nightingale 编译完成"


####################################################################
#                 运行阶段 (runtime) = ubuntu:lite                #
####################################################################
FROM iflyelf/ubuntu:lite

# 作者描述信息
LABEL org.opencontainers.image.authors="iflyelf" \
      org.opencontainers.image.vendor="iflyelf" \
      org.opencontainers.image.title="Nightingale with Event Aggregation" \
      org.opencontainers.image.description="Nightingale 监控系统 - 集成事件聚合功能, runtime on ubuntu:lite"

# 时区设置
ARG TZ=Asia/Shanghai
ENV TZ=$TZ
# 语言设置
ARG LANG=zh_CN.UTF-8
ENV LANG=$LANG

# 镜像变量
ARG DOCKER_IMAGE=iflyelf/nightingale
ENV DOCKER_IMAGE=$DOCKER_IMAGE
ARG DOCKER_IMAGE_OS=ubuntu
ENV DOCKER_IMAGE_OS=$DOCKER_IMAGE_OS
ARG DOCKER_IMAGE_TAG=lite
ENV DOCKER_IMAGE_TAG=$DOCKER_IMAGE_TAG

# 环境设置
ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=$DEBIAN_FRONTEND

# ***** 运行阶段按需依赖 *****
# n9e 为纯静态二进制, 无动态库依赖。运行所需:
#   bash            -> docker-entrypoint.sh 使用 #!/bin/bash
#   netcat-openbsd  -> entrypoint 中 WAIT_FOR 等待依赖服务(nc -z)所需
#   curl            -> HEALTHCHECK 健康检查所需
#   ca-certificates -> 访问 HTTPS / 各数据源校验证书所需
#   tzdata + locales-> TZ=Asia/Shanghai 与 LANG=zh_CN.UTF-8 生效所需
# 注: ubuntu:lite 已含 tini(作为 init), 故此处无需再装。
ARG RUNTIME_DEPS="bash netcat-openbsd curl ca-certificates tzdata locales"
ENV RUNTIME_DEPS=$RUNTIME_DEPS

RUN set -eux && \
    DEBIAN_FRONTEND=noninteractive apt-get update -qqy && \
    DEBIAN_FRONTEND=noninteractive apt-get install -qqy --no-install-recommends $RUNTIME_DEPS --option=Dpkg::Options::=--force-confdef && \
    for pkg in $RUNTIME_DEPS; do \
        if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then \
            echo "ERROR: 运行依赖未成功安装: $pkg" >&2 && exit 1; \
        fi; \
    done && \
    update-ca-certificates && \
    # 生成中文 locale, 避免 LANG=zh_CN.UTF-8 报错
    (locale-gen zh_CN.UTF-8 || true) && \
    echo "运行依赖验证通过" && \
    DEBIAN_FRONTEND=noninteractive apt-get -qqy autoremove --purge && \
    DEBIAN_FRONTEND=noninteractive apt-get -qqy autoclean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/* && \
    ln -sf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone

# 拷贝编译产物与配置
COPY --from=builder /opt/nightingale /opt/nightingale

#############################
#   运行时配置              #
#############################

WORKDIR /opt/nightingale

# 暴露端口 (19000: n9e 主服务, 18000: pushgw)
EXPOSE 19000 18000

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:19000/api/n9e/ping || exit 1

# 启动脚本
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["n9e"]
