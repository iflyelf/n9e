#############################
#   Nightingale 事件聚合版   #
#   模板参考: iflyelf/ubuntu-docker
#############################
ARG GO_VERSION=1.26.4
ARG BASE_IMAGE=ubuntu:noble

# APT 镜像源开关: true=阿里云(国内构建), false=官方源(CI/海外构建)
# CI 环境(GitHub Actions)默认使用官方源, 避免访问阿里云证书验证失败
ARG USE_CN_MIRROR=false

# 第一阶段: 构建阶段
FROM ${BASE_IMAGE} AS builder

ARG TARGETARCH
ARG TARGETVARIANT
ARG GO_VERSION
ARG USE_CN_MIRROR
ARG GOPROXY=https://goproxy.cn,direct

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    GOPROXY=${GOPROXY} \
    GOROOT=/opt/go \
    GOPATH=/opt/golang \
    PATH=/opt/go/bin:/opt/golang/bin:$PATH

# 安装构建依赖
RUN set -eux && \
    # 解决证书认证失败问题(参考 ubuntu-docker 模板)
    touch /etc/apt/apt.conf.d/99verify-peer.conf && \
    echo >>/etc/apt/apt.conf.d/99verify-peer.conf "Acquire { https::Verify-Peer false }" && \
    # 按需切换国内镜像源
    if [ "${USE_CN_MIRROR}" = "true" ]; then \
        sed -i 's@URIs: http://[a-z.]*\.ubuntu\.com/ubuntu/@URIs: https://mirrors.aliyun.com/ubuntu/@g' /etc/apt/sources.list.d/ubuntu.sources ; \
    fi && \
    apt-get update -qqy && \
    apt-get install -qqy --no-install-recommends \
        wget curl git ca-certificates build-essential python3 && \
    rm -rf /var/lib/apt/lists/*

# 安装 Go (映射 buildx TARGETARCH 到 Go 官方包名)
RUN set -eux && \
    case "${TARGETARCH}" in \
        amd64)   GO_ARCH=amd64   ;; \
        arm64)   GO_ARCH=arm64   ;; \
        arm)     GO_ARCH=armv6l  ;; \
        386)     GO_ARCH=386     ;; \
        *)       echo "不支持的架构: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    echo "目标架构: ${TARGETARCH} => Go 包: linux-${GO_ARCH}" && \
    wget --no-check-certificate https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz \
         -O /tmp/go.tar.gz && \
    tar xzf /tmp/go.tar.gz -C /opt && \
    rm -f /tmp/go.tar.gz && \
    ln -sf /opt/go/bin/* /usr/bin/ && \
    go version

WORKDIR /build

# 复制子模块代码和补丁脚本
COPY upstream /build/nightingale
COPY apply-aggregation-patch.py /build/

# 应用事件聚合补丁
RUN cd /build && \
    python3 apply-aggregation-patch.py

# 编译 Nightingale (前端 pub 目录已随上游代码提供, 无需 Node 构建)
RUN cd /build/nightingale && \
    go mod download && \
    make build && \
    make build-pushgw && \
    ls -lh n9e* && \
    echo "✅ 编译完成"

# 第二阶段: 运行阶段
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.authors="iflyelf" \
      org.opencontainers.image.vendor="iflyelf" \
      org.opencontainers.image.title="Nightingale with Event Aggregation" \
      org.opencontainers.image.description="Nightingale 监控系统 - 集成事件聚合功能"

ARG USE_CN_MIRROR
ARG TZ=Asia/Shanghai
ARG LANG=zh_CN.UTF-8

ENV TZ=$TZ \
    LANG=$LANG \
    DEBIAN_FRONTEND=noninteractive

# 安装运行时依赖
RUN set -eux && \
    # 解决证书认证失败问题
    touch /etc/apt/apt.conf.d/99verify-peer.conf && \
    echo >>/etc/apt/apt.conf.d/99verify-peer.conf "Acquire { https::Verify-Peer false }" && \
    # 按需切换国内镜像源
    if [ "${USE_CN_MIRROR}" = "true" ]; then \
        sed -i 's@URIs: http://[a-z.]*\.ubuntu\.com/ubuntu/@URIs: https://mirrors.aliyun.com/ubuntu/@g' /etc/apt/sources.list.d/ubuntu.sources ; \
    fi && \
    apt-get update -qqy && \
    apt-get install -qqy --no-install-recommends \
        ca-certificates \
        tzdata \
        locales \
        curl \
        tini && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/share/zoneinfo/${TZ} /etc/localtime && \
    echo ${TZ} > /etc/timezone && \
    locale-gen zh_CN.UTF-8

# 创建工作目录
RUN mkdir -p /opt/nightingale/etc \
             /opt/nightingale/logs \
             /opt/nightingale/data

WORKDIR /opt/nightingale

# 从构建阶段复制二进制文件
COPY --from=builder /build/nightingale/n9e /opt/nightingale/
COPY --from=builder /build/nightingale/n9e-pushgw /opt/nightingale/
COPY --from=builder /build/nightingale/etc /opt/nightingale/etc/
COPY --from=builder /build/nightingale/pub /opt/nightingale/pub/

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
