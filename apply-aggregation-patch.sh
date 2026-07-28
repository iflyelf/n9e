#!/usr/bin/env bash
#=================================================
# 描述: Nightingale 事件聚合功能补丁脚本
# 版本: 1.0.0
# 作者: iflyelf
# 功能: 自动为上游 nightingale 代码应用事件聚合功能
#=================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM_DIR="${SCRIPT_DIR}/upstream"
TARGET_FILE="${UPSTREAM_DIR}/alert/dispatch/dispatch.go"
PATCH_FILE="${SCRIPT_DIR}/aggregation.patch"

echo "🚀 开始应用事件聚合功能补丁..."
echo "📂 上游目录: ${UPSTREAM_DIR}"
echo "🎯 目标文件: ${TARGET_FILE}"
echo ""

# 检查文件是否存在
if [ ! -f "${TARGET_FILE}" ]; then
    echo "❌ 错误: 目标文件不存在: ${TARGET_FILE}"
    exit 1
fi

# 检查是否已经应用过补丁
if grep -q "AggregationKey" "${TARGET_FILE}"; then
    echo "✅ 补丁已存在，跳过应用"
    exit 0
fi

echo "🔧 应用补丁（使用 patch 文件）..."

# 应用 patch
if [ -f "${PATCH_FILE}" ]; then
    cd "${UPSTREAM_DIR}" && patch -p1 < "${PATCH_FILE}"
    echo "✅ 补丁应用成功！"
    exit 0
fi

echo "⚠️  patch 文件不存在，使用 sed 方式应用补丁..."

# 备份原文件
cp "${TARGET_FILE}" "${TARGET_FILE}.bak"

# 方法1: 在 init() 后添加聚合结构体
sed -i '/^func init() {/a\
\n// 聚合Key结构体\n// 用于唯一标识一组聚合事件\ntype AggregationKey struct {\n\tRuleName               string\n\tNotifyRuleId           int64\n\tNotifyConfigChannelId  int64\n\tNotifyConfigTemplateId int64\n\tNotifyChannelId        int64\n\tMessageTemplateId      int64\n}\n\nfunc (k AggregationKey) String() string {\n\treturn fmt.Sprintf("%s|%d|%d|%d|%d|%d", k.RuleName, k.NotifyRuleId, k.NotifyConfigChannelId, k.NotifyConfigTemplateId, k.NotifyChannelId, k.MessageTemplateId)\n}\n\n// 聚合事件结构体\ntype AggregatedEvents struct {\n\tEvents          []*models.AlertCurEvent\n\tFirstTime       int64\n\tNotifyRuleId    int64\n\tNotifyConfig    *models.NotifyConfig\n\tNotifyChannel   *models.NotifyChannelConfig\n\tMessageTemplate *models.MessageTemplate\n}
' "${TARGET_FILE}"

# 方法2: 在 RwLock 后添加聚合字段  
sed -i '/^\tRwLock sync.RWMutex$/a\
\n\taggrCache map[string]*AggregatedEvents \/\/ 聚合缓存\n\taggrLock  sync.Mutex                   \/\/ 聚合锁
' "${TARGET_FILE}"

# 方法3: 在 return notify 前添加初始化
sed -i '/^\treturn notify$/i\
\t\/\/ 初始化聚合缓存\n\tnotify.aggrCache = make(map[string]*AggregatedEvents)\n\n\t\/\/ 启动聚合发送协程\n\tnotify.StartAggregationSender()\n
' "${TARGET_FILE}"

# 方法4: 替换发送调用为聚合
sed -i 's/go SendByNotifyRule(e\.ctx, e\.userCache, e\.userGroupCache, e\.notifyChannelCache, e\.configCvalCache, \[\]\*models\.AlertCurEvent{eventCopy}, notifyRuleId,/\/\/ 聚合：不直接发送，加入缓存\n\t\t\t\te.AddToAggregation(eventCopy, eventCopy.RuleName, notifyRuleId, \&notifyRule.NotifyConfigs[i], notifyChannel, messageTemplate)/' "${TARGET_FILE}"

# 方法5: 在文件末尾添加聚合方法
cat >> "${TARGET_FILE}" << 'EOFAGGR'

// AddToAggregation 将事件加入聚合缓存
func (e *Dispatch) AddToAggregation(event *models.AlertCurEvent, ruleName string, notifyRuleId int64, notifyConfig *models.NotifyConfig, notifyChannel *models.NotifyChannelConfig, messageTemplate *models.MessageTemplate) {
	var messageTemplateId int64
	if messageTemplate != nil {
		messageTemplateId = messageTemplate.ID
	}

	key := AggregationKey{
		RuleName:               ruleName,
		NotifyRuleId:           notifyRuleId,
		NotifyConfigChannelId:  notifyConfig.ChannelID,
		NotifyConfigTemplateId: notifyConfig.TemplateID,
		NotifyChannelId:        notifyChannel.ID,
		MessageTemplateId:      messageTemplateId,
	}.String()

	e.aggrLock.Lock()
	defer e.aggrLock.Unlock()

	aggr, ok := e.aggrCache[key]
	if !ok {
		aggr = &AggregatedEvents{
			FirstTime:       time.Now().Unix(),
			NotifyRuleId:    notifyRuleId,
			NotifyConfig:    notifyConfig,
			NotifyChannel:   notifyChannel,
			MessageTemplate: messageTemplate,
		}
		e.aggrCache[key] = aggr
	}
	aggr.Events = append(aggr.Events, event)
}

// StartAggregationSender 启动定时批量发送协程
func (e *Dispatch) StartAggregationSender() {
	go func() {
		for {
			time.Sleep(10 * time.Second)
			now := time.Now().Unix()
			e.aggrLock.Lock()
			for key, aggr := range e.aggrCache {
				if now-aggr.FirstTime >= 60 && len(aggr.Events) > 0 {
					go SendByNotifyRule(e.ctx, e.userCache, e.userGroupCache, e.notifyChannelCache, e.configCvalCache, aggr.Events, aggr.NotifyRuleId, aggr.NotifyConfig, aggr.NotifyChannel, aggr.MessageTemplate)
					delete(e.aggrCache, key)
				}
			}
			e.aggrLock.Unlock()
		}
	}()
}
EOFAGGR

# 验证结果
if grep -q "AggregationKey" "${TARGET_FILE}" && grep -q "AddToAggregation" "${TARGET_FILE}"; then
    echo "✅ 补丁应用成功！"
    echo ""
    echo "📊 补丁统计："
    echo "  - 新增聚合结构体: AggregationKey, AggregatedEvents"
    echo "  - 新增方法: AddToAggregation, StartAggregationSender"
    echo "  - 修改发送逻辑: SendByNotifyRule -> AddToAggregation"
    echo ""
    rm -f "${TARGET_FILE}.bak"
    exit 0
else
    echo "❌ 补丁应用失败，恢复原文件"
    mv "${TARGET_FILE}.bak" "${TARGET_FILE}"
    exit 1
fi
