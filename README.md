# xavier_reminder

显式定时提醒 + Web 日历面板。给 AstrBot（v4+）用。

跟 `wakeup` 那种"模型自己决定啥时候主动说话"不一样，这个是**用户明确告诉我"几点做什么"**的闹钟系统。设完之后到点了，插件伪装成一条消息推给 LLM，LLM 用自己的人格自然地把提醒说出来，不是硬邦邦的"⏰ 提醒你 xxx"。

---

## 能做什么

- **单次提醒**：跟 LLM 说"7 月 20 号下午三点提醒我拿快递"、"两小时后提醒我关火"
- **每日循环**：跟 LLM 说"每天早上八点提醒我吃早饭"
- **模糊取消**：跟 LLM 说"把每天早饭那个取消掉"，不用记 ID
- **临时跳过**：跟 LLM 说"明天早上不用提醒"，只跳明天，后天继续；也支持"这周都别提醒"、"下周一之前都别提"
- **查看列表**：`/reminders` 直接查当前会话所有提醒（零 LLM 调用，不费钱）
- **Web 日历**：浏览器打开 `http://IP:8899/reminder/`，日历视图看所有提醒，能加、能改、能删、能跳过

---

## 安装

1. 把整个 `xavier_reminder` 文件夹放到 `AstrBot/data/plugins/`
2. 依赖会自动装：`aiohttp>=3.9`
3. 重启 AstrBot 或热重载插件

---

## 配置（Dashboard 里改）

打开插件配置页，几个关键项：

- **trigger_prompt_template**（触发时说给模型听的话）
  时间到的时候，插件会把这段话伪装成一条消息发给 LLM，让 TA 自然地把提醒说出来。
  里面 `{content}` 会被替换成提醒内容，`{time}` 换成时间，`{type}` 换成"每日"或"单次"。
  可以随便改成你喜欢的语气。

- **scan_interval_seconds**（多久扫一次）
  后台每几秒检查一次有没有提醒到点。默认 30 秒。

- **no_interrupt_seconds**（不打断阈值）
  如果你正在跟 LLM 聊天，最近这么多秒内有活动，就先不触发提醒，等你聊完。默认 60 秒。设 0 就是立刻插进来不管你在不在聊。

- **enable_confirmation**（确认机制，默认关）
  开了以后，提醒完你如果一直不回应，过一段时间会再提一次。当前版本占位，未完全实现。

- **max_reminders_per_session**（单会话最多几条，默认 50）
  防止无限塞。

- **enable_web / web_host / web_port / web_username / web_password / web_base_path**
  Web 面板相关。**密码必须填**，不填 Web 不会启动（安全考虑）。

---

## Web 面板

配好 `web_password` 之后，重载插件。打开：

```
http://你的服务器IP:8899/reminder/
```

浏览器会弹 Basic Auth，输你设的用户名密码。

界面里能干嘛：

- 点日历上任意一天空白格 → 新建提醒
- 点已有提醒色条 → 编辑 / 删除 / 添加跳过日期
- 右侧列表按时间排序显示全部提醒
- 顶部下拉筛选会话（多个 IM 会话时用）
- 每 30 秒自动刷新一次（跟 LLM 那边共享同一份数据，改哪边另一边都看得到）

**颜色约定：**
- 粉色 = 每日循环
- 蓝色 = 单次
- 灰色划掉 = 那天被跳过了

---

## 跟 LLM 怎么说

平常怎么聊就怎么聊，不需要记指令。举几个例子：

```
你："每天早上八点提醒我吃早饭"
LLM：好的记住啦。（后台悄悄调用了 add_reminder）

你："明天下午三点提醒我拿快递就行"
LLM：嗯嗯没问题。（add_reminder once）

你："两小时后提醒我关火"
LLM：好嘞。（LLM 自己算出具体时间再调用）

你："每天早饭那个取消掉吧，最近不吃了"
LLM：好，取消啦。（cancel_reminder query=早饭）

你："明天早上那个不用提醒了"
LLM：好，明天跳过。（skip_reminder query=早饭 skip_type=once）

你："这周都别提醒我早饭了"
LLM：好，这周都不提。（skip_reminder skip_type=count count=7）
```

---

## 指令列表

- `/reminders` — 列出当前会话所有提醒
- `/reminder_clear` — 提示清空
- `/reminder_clear_yes` — 确认清空
- `/reminder_web` — 打印 Web 面板访问地址

---

## 数据存哪

`AstrBot/data/plugin_data/xavier_reminder/reminders.json`

想手动改也行，改完等下次扫描（≤30s）就生效。格式：

```json
{
  "r_20260714_100000_abc": {
    "id": "r_20260714_100000_abc",
    "umo": "aiocqhttp:FriendMessage:12345",
    "type": "daily",
    "content": "吃早饭",
    "next_fire_ts": 1721001600.0,
    "hour": 8,
    "minute": 0,
    "skip_dates": ["2026-07-15"],
    "created_at": 1720953600.0,
    "created_by": "llm",
    "note": null
  }
}
```

---

## 会话隔离

插件遵守 AstrBot 的会话级插件开关。如果某个会话把本插件禁了：

- LLM Tool 会拒绝调用
- 系统提示词不会注入
- 后台调度到点了也不触发（但任务保留，等你重新启用就继续跑）

---

## 常见问题

**Q：提醒到点了没反应？**
A：先看日志有没有 `[reminder]` 前缀的行。常见原因：
- CQHttp 实例还没就绪 → 让 bot 先收一条你的消息帮它捕获引用
- 会话被禁用了 → 检查会话级插件开关
- 时间没到 → `/reminders` 看下次触发时间

**Q：Web 面板打不开？**
A：
- 检查 `web_password` 是否设置
- 检查 `web_port` 是否被其他服务占用
- 云服务器要在安全组/防火墙开放对应端口

**Q：怎么改触发时的话术？**
A：改配置项 `trigger_prompt_template`，改完热重载插件。

**Q：会不会重启就丢？**
A：不会。全部持久化到 JSON 文件，启动时自动恢复。过期的会尽快补触发一次。

---

## 后续计划

- 二期：经期周期模块（独立 tab，日历上标记不同阶段，可选自动关怀）
- 每周 / 每月 / 农历循环
- Web 面板的深色模式和移动端优化

有 bug 或需求直接找作者。

