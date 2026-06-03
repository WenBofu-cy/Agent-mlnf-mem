# Agent-mlnf-mem：AI Agent 双漏斗记忆中枢

**EM-Core 记忆中枢 · AI Agent 专项实现**

> 版本：V1.0
> 原创提出者：文波福
> 开源协议：CC BY-NC 4.0（署名-非商业性使用 4.0 国际许可证）
> 所属体系：EM-Core Agent 通用智能系统
> 配套仓库：[EM-Core-Agent-Spec](https://github.com/expanding-research/em-core-agent-spec)（总规范）｜ [Agent-ecc-brain](https://github.com/expanding-research/agent-ecc-brain)（认知大脑）｜ [Agent-mcc-exec](https://github.com/expanding-research/agent-mcc-exec)（行动执行层）


## 一、仓库定位

本仓库是 EM-Core Agent 的 **双漏斗记忆中枢**，负责存储与管理用户画像、对话历史、任务执行经验及知识库。采用漏斗一“懂人”（用户偏好画像）、漏斗二“练己”（任务自成长经验）的双漏斗架构，五层单向晋升，三维重要度驱动。记忆数据物理隔离，确保隐私安全。


## 二、核心架构

- **漏斗一（用户画像漏斗）**：记录用户偏好、习惯、显式与隐式反馈
- **漏斗二（任务经验漏斗）**：沉淀任务执行策略、工具调用成功/失败案例
- **五层晋升通路**：L1(临时)→L2(近期)→L3(中期)→L4(长期)→L5(核心)
- **三维重要度公式**：I = I₀ + α·S + β·V + γ·C


## 三、与认知大脑及行动执行层的协同

- 接收 [Agent-ecc-brain](https://github.com/expanding-research/agent-ecc-brain) 的记忆查询与写入请求
- 为 [Agent-mcc-exec](https://github.com/expanding-research/agent-mcc-exec) 提供历史执行偏好（可选）


## 四、开源协议与商业授权

基础版采用 **CC BY-NC 4.0** 协议开源。商业使用需获得 [商业授权](../LICENSE-COMMERCIAL.md)。


## 五、联系方式

- **原创提出者**：文波福
- **邮箱**：710705008@qq.com


#### 介绍
{**以下是 Gitee 平台说明，您可以替换此简介**
Gitee 是 OSCHINA 推出的基于 Git 的代码托管平台（同时支持 SVN）。专为开发者提供稳定、高效、安全的云端软件开发协作平台
无论是个人、团队、或是企业，都能够用 Gitee 实现代码托管、项目管理、协作开发。企业项目请看 [https://gitee.com/enterprises](https://gitee.com/enterprises)}

#### 软件架构
软件架构说明


#### 安装教程

1.  xxxx
2.  xxxx
3.  xxxx

#### 使用说明

1.  xxxx
2.  xxxx
3.  xxxx

#### 参与贡献

1.  Fork 本仓库
2.  新建 Feat_xxx 分支
3.  提交代码
4.  新建 Pull Request


#### 特技

1.  使用 Readme\_XXX.md 来支持不同的语言，例如 Readme\_en.md, Readme\_zh.md
2.  Gitee 官方博客 [blog.gitee.com](https://blog.gitee.com)
3.  你可以 [https://gitee.com/explore](https://gitee.com/explore) 这个地址来了解 Gitee 上的优秀开源项目
4.  [GVP](https://gitee.com/gvp) 全称是 Gitee 最有价值开源项目，是综合评定出的优秀开源项目
5.  Gitee 官方提供的使用手册 [https://gitee.com/help](https://gitee.com/help)
6.  Gitee 封面人物是一档用来展示 Gitee 会员风采的栏目 [https://gitee.com/gitee-stars/](https://gitee.com/gitee-stars/)
