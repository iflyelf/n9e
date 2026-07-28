#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nightingale 事件聚合功能补丁脚本
自动为上游 nightingale 代码应用事件聚合功能
"""

import os
import sys
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 支持通过命令行参数或环境变量指定目标文件路径
if len(sys.argv) > 1:
    TARGET_FILE = sys.argv[1]
elif os.environ.get('TARGET_FILE'):
    TARGET_FILE = os.environ.get('TARGET_FILE')
else:
    # 默认路径（相对于脚本目录）
    TARGET_FILE = os.path.join(SCRIPT_DIR, "upstream/alert/dispatch/dispatch.go")

BACKUP_FILE = TARGET_FILE + ".bak"

# 聚合结构体定义
AGGREGATION_STRUCTS = '''
// 聚合Key结构体
// 用于唯一标识一组聚合事件
type AggregationKey struct {
	RuleName               string
	NotifyRuleId           int64
	NotifyConfigChannelId  int64
	NotifyConfigTemplateId int64
	NotifyChannelId        int64
	MessageTemplateId      int64
}

func (k AggregationKey) String() string {
	return fmt.Sprintf("%s|%d|%d|%d|%d|%d", k.RuleName, k.NotifyRuleId, k.NotifyConfigChannelId, k.NotifyConfigTemplateId, k.NotifyChannelId, k.MessageTemplateId)
}

// 聚合事件结构体
type AggregatedEvents struct {
	Events          []*models.AlertCurEvent
	FirstTime       int64
	NotifyRuleId    int64
	NotifyConfig    *models.NotifyConfig
	NotifyChannel   *models.NotifyChannelConfig
	MessageTemplate *models.MessageTemplate
}
'''

# 聚合字段
AGGREGATION_FIELDS = '''
	aggrCache map[string]*AggregatedEvents // 聚合缓存
	aggrLock  sync.Mutex                   // 聚合锁
'''

# 聚合初始化
AGGREGATION_INIT = '''\t// 初始化聚合缓存
	notify.aggrCache = make(map[string]*AggregatedEvents)

	// 启动聚合发送协程
	notify.StartAggregationSender()

'''

# 聚合方法
AGGREGATION_METHODS = '''
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
'''

def apply_patch():
    print("🚀 开始应用事件聚合功能补丁...")
    print(f"🎯 目标文件: {TARGET_FILE}\n")

    if not os.path.exists(TARGET_FILE):
        print(f"❌ 错误: 目标文件不存在: {TARGET_FILE}")
        return False

    # 读取原文件
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已应用
    if 'AggregationKey' in content:
        print("✅ 补丁已存在，跳过应用")
        return True

    # 备份
    with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print("🔧 应用补丁...")

    # 1. 在 init() 函数后添加聚合结构体
    content = re.sub(
        r'(func init\(\) \{[^}]+\})',
        r'\1' + AGGREGATION_STRUCTS,
        content,
        count=1
    )

    # 2. 在 RwLock 后添加聚合字段
    content = re.sub(
        r'(\tRwLock sync\.RWMutex\n)',
        r'\1' + AGGREGATION_FIELDS,
        content,
        count=1
    )

    # 3. 在 return notify 前添加初始化 (NewDispatch函数内)
    content = re.sub(
        r'(\n\treturn notify\n)',
        '\n' + AGGREGATION_INIT + r'\treturn notify\n',
        content,
        count=1
    )

    # 4. 替换 SendByNotifyRule 调用为聚合
    pattern = r'go SendByNotifyRule\(e\.ctx, e\.userCache, e\.userGroupCache, e\.notifyChannelCache, e\.configCvalCache, \[\]\*models\.AlertCurEvent\{eventCopy\}, notifyRuleId, &notifyRule\.NotifyConfigs\[i\], notifyChannel, messageTemplate\)'
    replacement = '// 聚合：不直接发送，加入缓存\n\t\t\t\te.AddToAggregation(eventCopy, eventCopy.RuleName, notifyRuleId, &notifyRule.NotifyConfigs[i], notifyChannel, messageTemplate)'
    content = re.sub(pattern, replacement, content)

    # 5. 在文件末尾添加聚合方法
    content = content.rstrip() + '\n' + AGGREGATION_METHODS

    # 写回文件
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    # 验证
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        new_content = f.read()

    if 'AggregationKey' in new_content and 'AddToAggregation' in new_content and 'StartAggregationSender' in new_content:
        print("✅ 补丁应用成功！\n")
        print("📊 补丁统计：")
        print("  - 新增聚合结构体: AggregationKey, AggregatedEvents")
        print("  - 新增方法: AddToAggregation, StartAggregationSender")
        print("  - 修改发送逻辑: SendByNotifyRule -> AddToAggregation")
        os.remove(BACKUP_FILE)
        return True
    else:
        print("❌ 补丁应用失败，恢复原文件")
        os.rename(BACKUP_FILE, TARGET_FILE)
        return False

if __name__ == '__main__':
    success = apply_patch()
    sys.exit(0 if success else 1)
