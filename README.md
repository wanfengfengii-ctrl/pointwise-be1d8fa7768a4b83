# 河口计量日账

河口计量中心的入仓员接收各站的整升观测，日结员分版封账，报送员向独立接收账发送增减额，复核员凭封账号领回定稿与回执。站组可继续提交同日勘误，次班也可在原载荷到期后接着报送。工程使用 Python 3.11 标准库和 SQLite。

## 当班流程

1. 入仓员提交具名日批，登记初值或勘误。
2. 入仓端校验整批，登记原载荷并更新具名行的现行值。原样重投领取原接纳结果。
3. 日结员封账，固定本日现行值之和、封账号、日内顺位和来源批次水位。
4. 报送员推进最早未核版，凭前版的确认事实固定本版净差及请求号。
5. 接收端独立提交实际金额及原回执，同参重投领取原回执。
6. 报送端将原回执与该版绑定并推进本地确认前沿。未知结局留在待核，重开后继续原请求。
7. 复核员按原封账号核对定稿、差额和对应外端流水，最后一笔同时可核对当前日余额。
8. 复核员提供保留截止日，清理到期且已被封账覆盖的原载荷并登记。
9. 下一班重开工作账和接收账，延续报送与领取。

## 运行

```sh
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B demo.py
python3 -B init_workspace.py work
```

平台提供 `check_project.py` 作为标准回归入口。演示读取 `samples/shift.json`，使用独立的临时工作账和接收账，重开后再读取实际数据。`2 + 3 = 5`，第二行更正为 `10` 后是 `12`，再更正为 `7` 后是 `9`。沿相邻定稿应报 `+5`、`+7`、`-3`。第四次封账仍为 `9`，须另收 `0` 额回执。另一日 `4` 升从零起报。

## Python API

`audit.workspace.initialize_workspace(root)` 可重复初始化。`audit.workspace.open_workspace(root)` 返回上下文管理器，提供 `work.local` 与 `work.external`，退出时关闭两条连接。目录内保存 `warehouse.sqlite3` 和 `receiver.sqlite3`。初始化只补齐结构，演示显式导入样例。每个工作者使用自己的连接，公开写操作管理自己的事务。直接接收端调用应使用调用方拥有的请求标识，`seal:` 前缀由计量报送使用。

| 入口 | 输入和返回 |
| --- | --- |
| `intake.batches.apply(local, day, batch_id, kind, rows)` | `kind` 为 `initial` 或 `correction`。`rows` 为列表或元组，依次包含 `(station, name, litres)`。返回 `BatchResult`，含本日批次顺位和实际改变读数的勘误行数。 |
| `close.finalize.seal(local, day, seal_id)` | 返回 `Revision`，含日期、封账号、日内顺位、总量和来源水位。每次成功均产生一版独立定稿。 |
| `dispatch.submit.advance(local, external, day)` | 一次推进一版。`AdvanceResult.state` 为 `idle`、`pending` 或 `confirmed`，有确认时附原 `Receipt`。可用 `after_post(receipt)` 在外端已提交、本地尚未确认时记录调用或通过 `ReplyLost` 注入丢回包。 |
| `receiver.requests.post(external, request_id, day, delta)` | 接纳有符号的整数差额，返回原 `Receipt`。同号同参重投不重复增加流水。 |
| `receiver.balances.balance(external, day)` | 返回 `DayBalance`，含该日实际余额及按入账顺序排列的流水。 |
| `audit.lookup.lookup(local, seal_id)` | 返回该版 `Review`，未知封账号返回 `None`。`pending` 无回执及金额，`confirmed` 提供原回执和净差。 |
| `audit.reconcile.reconcile_posting(external, review)` | 按请求号匹配外端流水并核对日期、入账顺位、金额。待核版尚无匹配结论，历史版的当前余额比较为 `None`。 |
| `audit.purge.purge(local, cutoff)` | 返回 `PurgeResult`，列出本次清理的批号与覆盖封账号。`audit.purge.purge_history(local)` 读取已提交清理登记。 |

```python
from audit.lookup import lookup
from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.submit import advance
from intake.batches import apply

initialize_workspace('work')
with open_workspace('work') as work:
    apply(work.local, '2025-03-01', 'east-boot', 'initial', [('东站', '甲', 5)])
    revision = seal(work.local, '2025-03-01', 'east-seal-1')
    advance(work.local, work.external, revision.day)
    review = lookup(work.local, revision.seal_id)
    print(review.revision.total, review.delta, review.receipt.receipt_id)
```

日期和截止日按 `YYYY-MM-DD` 使用。身份为含有效字符的 Unicode 文本，站行身份由日期、站点及行名组成。同一批内一份身份只能出现一次。首录时身份须空闲，勘误目标须存在，同值更正的变化数为零。整数读数使用 Python `int`，空行集的合计为零。读数、总量、差额和余额均以升计，SQLite 金额列以十六进制文本保存，求和和相减在 Python 中完成。

相同批号的日期、批种、行序、站行和金额均须匹配原接纳结果。清理删除已覆盖的原载荷和原行，保留重投摘要、唯一现行值、定稿、出站绑定和回执。截止日当天仍在保留期。有成功定稿但超出其水位的新批仍保留，待后续封账覆盖。清理资格独立于报送状态。

输入错误抛出 `meter_core.InputError`，身份冲突或勘误目标不存在抛出 `meter_core.ConflictError`，回执一致性错误抛出 `meter_core.EvidenceError`。SQLite 操作失败传播数据库错误，本地失败事务回滚。如果外端已提交而本地确认失败，原请求仍可重办，按号在确认前显示待核。两侧事务独立，所以接收账可在本地确认前已有入账。V1 处理单日逐版报送及核账。
