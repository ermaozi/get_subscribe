## ⚠️ 重要警告（请先看）

- 本页免费订阅链接通常会被大量用户同时使用，不具备稳定和安全条件。
- 高复用节点常见问题：IP 污染、出口被风控、速度波动、隐私不可控。
- 内容创作者（短视频、直播、自媒体、电商运营）请不要使用此类免费订阅作为生产网络。
- 原因很直接：IP 不干净或被滥用历史命中风控后，容易触发平台限流、账号异常或登录验证升级。
- 链接来自网络，仅作学习与测试用途，使用时请务必遵守当地法律法规。

## 翻墙交流群（Telegram）

[@fanqiangjiaoliu](https://t.me/fanqiangjiaoliu)

## 免费机场订阅链接（12小时更新）

- Clash 订阅链接

```
https://www.ermao.net/sub/clash/ermao.net
```

- V2Ray 订阅链接

```
https://www.ermao.net/sub/v2ray/ermao.net
```

## 使用体验截图

低峰期可以看视频，高峰期可能会有点卡顿。

![免费机场 示例图片](https://image.ermao.net/images/article/oh8wwokl/image.png)

![免费机场 示例图片](https://image.ermao.net/images/article/oh8wwokl/image-1.png)

## 客户端使用教程

- 📱 [Android](https://www.ermao.net/article/eh8f4n86/)
- 🖥 [Windows](https://www.ermao.net/article/0gematwc/)
- 🍎 [iOS](https://www.ermao.net/article/z747kgjd/)

## 常见问题（FAQ）

### 免费机场适合长期使用吗？

不适合。免费订阅更适合临时测试或短时应急，长期使用建议选择口碑稳定的付费服务。

### 为什么创作者不建议使用免费订阅？

因为共享出口 IP 往往历史复杂，命中平台风控概率更高，容易出现内容限流、账号登录异常等问题。

## 付费订阅推荐

我搜罗的一些比较便宜好用的机场，觉得免费订阅不好使的朋友们可以在这里面找找。

[https://www.ermao.net/posts/vpn](https://www.ermao.net/posts/vpn)

## 采集与发布架构

本仓库通过 GitHub Actions 每 12 小时采集长风分享、
[NoMoreWalls](https://github.com/peasoft/NoMoreWalls) 和
[ProxyPool](https://github.com/snakem982/proxypool) 的公开订阅。
BestClash 不在采集来源中。

Clash 按节点配置去重（忽略名称），重名节点自动改名，并加入原有节点选择组；
保留第一个可用来源的分流规则。V2Ray 兼容明文和 Base64 来源，按完整 URI 去重，
统一保存为主项目现有的明文节点列表。一个来源失败会继续其他来源，
缺少任一种有效订阅时保留原有文件并令任务失败。格式检查不代表节点连通或速度保证。

当前公开 `/sub/` 地址由独立 Cloudflare Worker `sub` 提供，Worker 自行采集并写入 R2，
不是从本仓库读取文件。因此本仓库增加来源不会自动同步到线上 Worker；
GitHub 版本可通过仓库中的 `subscribe/clash.yml` 和 `subscribe/v2ray.txt` 获取。

使用 Python 3.12 执行本地检查：

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

保留 `SUBSCRIBE_PROXY` 代理环境变量。GitHub 工作流使用内置 `GITHUB_TOKEN`，
无需额外的 `TOKEN` Secret。写入工作流共享并发组，避免同时更新分支；
原有每周清理提交历史行为保持不变。频繁手动触发时，GitHub 可能替换仍在排队的任务。


## 连通性分类

每次自动采集后运行 `python check_nodes.py`，保留 `subscribe/` 下的完整订阅，
另生成以下三个目录（各含 `clash.yml` 和 `v2ray.txt`）：

- `subscribe/reachable/`：从本次运行机器可以建立 TCP 连接的节点。
- `subscribe/unreachable/`：TCP 连接或 DNS 解析失败的节点。
- `subscribe/untested/`：UDP/QUIC 协议、无法解析地址或非公网地址，不判定为不可用。

`subscribe/health.json` 保存检测时间和分类数量。同一主机端口只检查一次，
最多并发 24 个端点，每个端点的 TCP 连接总预算为 3 秒（不含系统 DNS 解析时间）。
这不是代理认证、出口访问或速度测试；可连接不等于代理可用，失败也可能是运行机器的网络限制。
