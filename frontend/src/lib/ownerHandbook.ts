export type HandbookCommand = {
  command: string;
  meaning: string;
  risk: "read" | "controlled" | "high";
};

export type HandbookGroup = {
  title: string;
  items: HandbookCommand[];
};

export const HANDBOOK_RULES = [
  "只在和机器人 2000000002 的私聊里发斜杠指令。群里同一句话不会当命令。",
  "现在会自动回这个机器人的全部私聊，以及群里真实 @ 机器人。不 @ 不回。",
  "搜索、文生图、隐私删除执行、OCR 关着。空间采集必须指定某个 QQ。",
  "仪表盘备注只给你认人，不会进模型，也不会改变谁能被回复。",
  "要停自动回复：/机器人 暂停。确认高风险动作仍要 /确认 <审批码>。",
];

export const HANDBOOK_GROUPS: HandbookGroup[] = [
  {
    title: "先看这里",
    items: [
      { command: "/帮助", meaning: "看常用指令和手册入口", risk: "read" },
      { command: "/状态", meaning: "看身份与好友概览", risk: "read" },
      { command: "/机器人 状态", meaning: "看回复模式和是否暂停", risk: "read" },
      { command: "/机器人 暂停", meaning: "立刻停模型和外发", risk: "controlled" },
      { command: "/机器人 恢复", meaning: "从急停恢复", risk: "controlled" },
    ],
  },
  {
    title: "认人",
    items: [
      { command: "/用户 <QQ> 状态", meaning: "看这个人的授权和关系", risk: "read" },
      { command: "/用户 <QQ> 备注 <名字>", meaning: "给这个 QQ 起你认得的名字", risk: "controlled" },
      { command: "/用户 <QQ> 备注 清除", meaning: "去掉备注，列表只显示 QQ", risk: "controlled" },
      { command: "/群 <QQ> 备注 <名字>", meaning: "给群号起你认得的名字", risk: "controlled" },
    ],
  },
  {
    title: "用户授权",
    items: [
      { command: "/用户 <QQ> 标记旧友", meaning: "手动标成旧友", risk: "controlled" },
      { command: "/用户 <QQ> 历史 拉取", meaning: "按已有额度拉一次历史，只记条数", risk: "controlled" },
      { command: "/用户 <QQ> 空间资料 预览", meaning: "指定后才翻这个人的空间", risk: "controlled" },
    ],
  },
  {
    title: "确认与拒绝",
    items: [
      { command: "/确认 <审批码>", meaning: "批准刚才那条高风险动作", risk: "high" },
      { command: "/拒绝 <审批码>", meaning: "拒绝，不执行", risk: "high" },
    ],
  },
  {
    title: "外发与主动",
    items: [
      { command: "/外发 状态", meaning: "看出站发送是否开着", risk: "read" },
      { command: "/主动 自动 状态", meaning: "看主动调度", risk: "read" },
      { command: "/主动 创建 <QQ> <YYYY-MM-DD HH:MM> <内容>", meaning: "预约一条主动私聊", risk: "controlled" },
    ],
  },
  {
    title: "空间与汇报",
    items: [
      { command: "/空间 队列", meaning: "看说说任务", risk: "read" },
      { command: "/空间 发布 <内容>", meaning: "先出审批码，确认后才发", risk: "high" },
      { command: "/汇报 队列", meaning: "看主号汇报", risk: "read" },
    ],
  },
];
