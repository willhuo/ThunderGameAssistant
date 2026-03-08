# 游戏服务自动化工具

## 功能概述

本工具是一个游戏服务自动化工具，主要用于处理与迅雷游戏中心相关的操作，包括：

- 实名认证（防沉迷身份认证）
- 获取游戏信息
- 获取游戏URL（触发游戏开始）
- 发送游戏开始上报
- 发送心跳请求（保持游戏在线状态）
- 查询游戏进度
- 领取VIP奖励
- 会员领取资格判断（基于心跳速率和在线时长）

## 代码结构

### 主要类和方法

#### GameService类

**初始化参数：**
- `cookies`: 包含sessionid, userid, peerid的字典
- `gameid`: 游戏ID
- `task_no`: 任务ID
- `proxies`: 代理设置（可选）
- `use_proxy`: 是否使用代理（默认False）
- `proxy_city`: 代理城市名称，如'厦门'、'广州'等（默认None，使用广州）
- `card_key`: 卡密（用于日志标识）

**核心方法：**

1. **realname_bind()**: 实名认证绑定，可自动从数据库获取身份证信息
2. **get_game_info()**: 获取游戏信息，返回游戏名称
3. **get_game_url()**: 获取游戏URL，触发游戏开始
4. **start_game_report()**: 发送开始游戏上报
5. **play()**: 查询游戏进度
6. **get_token()**: 获取心跳所需的token
7. **send_heartbeat()**: 发送心跳请求，保持游戏在线状态
8. **get_vip()**: 领取VIP奖励
9. **run()**: 运行完整的充值流程
10. **load_online_time()**: 加载之前的累计在线时长
11. **save_online_time()**: 保存累计在线时长
12. **update_heartbeat_rate()**: 更新心跳速率
13. **update_online_time()**: 更新在线时长
14. **check_vip_eligibility()**: 检查会员领取资格

### 依赖模块

- `requests`: 用于发送HTTP请求
- `json`: 用于处理JSON数据
- `hashlib`: 用于生成MD5签名
- `time`: 用于处理时间相关操作
- `sys`, `os`: 用于处理系统路径和环境
- `re`: 用于正则表达式操作
- `base64`: 用于Base64编码
- `urllib.parse`: 用于URL解析

### 外部依赖

- `app.utils.ip_helper.IPHelper`: 用于获取代理IP
- `app.utils.logger`: 用于日志记录
- `app.utils.card_logger.card_log`: 用于卡密专属日志
- `app.core.config.settings`: 用于获取配置信息
- `app.core.database.SessionLocal`: 用于数据库操作
- `app.models.database.IdCard`: 身份证模型
- `app.utils.time_helper.get_beijing_date`: 用于获取北京时间

## 使用方法

### 基本使用

```python
from app.services.game_service import GameService

# 初始化游戏服务
cookies = {
    'sessionid': 'your_session_id',
    'userid': 'your_user_id',
    'peerid': 'your_peer_id'
}
game_service = GameService(
    cookies=cookies,
    gameid='123456',
    task_no='task_123',
    use_proxy=True,
    proxy_city='广州',
    card_key='card_123'
)

# 运行完整流程
game_service.run()
```

### 单独使用各功能

```python
# 实名认证
game_service.realname_bind()

# 获取游戏信息
game_name = game_service.get_game_info()

# 获取游戏URL
game_service.get_game_url()

# 发送游戏开始上报
game_service.start_game_report()

# 查询游戏进度
progress = game_service.play()

# 获取token
token = game_service.get_token()

# 发送心跳
game_service.send_heartbeat(token)

# 领取VIP奖励
game_service.get_vip('task_123')
```

## 会员领取资格判断

### 判断条件

用户满足以下任一条件时允许领取会员：

1. **累计心跳次数条件**：用户的累计心跳次数达到或超过65次
2. **在线时长条件**：用户当前任务在线时长累计达到或超过10分钟

### 实现机制

1. **心跳数据监测**：
   - 每次发送心跳时记录心跳次数
   - 累计心跳次数
   - 当累计心跳次数达到或超过65次时触发会员领取资格

2. **在线时长计时**：
   - 记录每次心跳的时间
   - 计算两次心跳之间的时间差并累计
   - 每30秒保存一次累计在线时长到本地文件
   - 应用启动时加载之前的累计在线时长

3. **资格判断逻辑**：
   - 每次发送心跳后检查会员领取资格
   - 满足任一条件即触发会员领取资格
   - 领取VIP前再次检查资格

4. **用户交互提示**：
   - 心跳日志中显示当前心跳速率和累计在线时长
   - 满足领取条件时显示明确的提示信息

### 边界条件处理

- 限制单次增加的在线时长，防止异常情况
- 网络中断后能够恢复之前的累计时长数据
- 应用重启后能够加载之前的累计在线时长

## 注意事项

1. **代理设置**：如果启用代理，需要确保`IPHelper`能够正确获取代理IP
2. **实名认证**：需要确保数据库中有可用的身份证信息
3. **网络稳定性**：工具内置了重试机制，但仍需确保网络稳定
4. **日志记录**：工具会记录详细的日志，便于排查问题
5. **错误处理**：工具会捕获并处理各种异常，确保流程能够继续执行
6. **会员领取**：需要满足心跳速率或在线时长条件才能领取VIP

## 配置说明

- `MAX_RETRY_COUNT`: 最大重试次数，默认10
- `RETRY_DELAY`: 重试间隔（秒），默认2
- `DEFAULT_PROXY_AREA`: 默认代理地区，从配置中获取

## 故障排除

1. **实名认证失败**：检查数据库中是否有可用的身份证信息
2. **网络错误**：检查网络连接，或尝试更换代理
3. **心跳失败**：检查token获取是否成功
4. **VIP领取失败**：检查账号是否存在风险，或游戏进度是否达到100%

## 日志说明

工具会输出两种日志：
1. 全局日志：记录所有操作
2. 卡密专属日志：记录特定卡密的操作

日志级别包括：info、warning、error
