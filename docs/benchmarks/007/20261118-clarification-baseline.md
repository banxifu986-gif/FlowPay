# 澄清机制统计报告

生成时间：2026-11-18 13:20 UTC。覆盖率三处并集（ask_user / clarification.decided / 检查点 answers），只统计确实发过问的多轮题（SC-1），已回答项不算漏问；单轮等待不计入覆盖分子/分母，仍计入等待轮统计。等待轮带结论按用户可见正文判定。旧臂没有决策事件与检查点澄清段，数字仅作基线参考。

## A

- 会话 96，匹配 96，跳过 0
- 多轮发问题数 60；整题全覆盖 0/60；类别级覆盖 107/322
- 重复问 12 处
- 等待轮 101（多轮 81；其中多轮最终轮 7），带结论 0，不带结论 101
- 单轮等待轮数 20（不计入覆盖分母）

| 类别 | 需要该类的发问题数 | 已覆盖 | 缺失 |
| --- | ---: | ---: | ---: |
| account_context | 60 | 60 | 0 |
| api_version | 22 | 0 | 22 |
| goal | 60 | 0 | 60 |
| identifiers | 60 | 47 | 13 |
| occurrence_time | 60 | 0 | 60 |
| symptom_details | 60 | 0 | 60 |

重复问明细：

- FP-BM-DG-0208：identifiers
- FP-BM-MS-0024：identifiers
- FP-BM-MH-0015：identifiers
- FP-BM-MH-0039：identifiers
- FP-BM-TV-0144：identifiers
- FP-BM-MH-0011：identifiers
- FP-BM-MH-0035：identifiers
- FP-BM-MS-0032：identifiers
- FP-BM-MS-0012：identifiers
- FP-BM-MS-0016：account_context
- FP-BM-MS-0016：identifiers
- FP-BM-MH-0031：identifiers

等待轮不带结论的题（含单轮）：FP-BM-TV-0108、FP-BM-TV-0104、FP-BM-MS-0060、FP-BM-DG-0204、FP-BM-TV-0160、FP-BM-SR-0037、FP-BM-MS-0044、FP-BM-MH-0055、FP-BM-MS-0036、FP-BM-SR-0029、FP-BM-DG-0232、FP-BM-SR-0005、FP-BM-MH-0007、FP-BM-MD-0018、FP-BM-DG-0236、FP-BM-TV-0132、FP-BM-DG-0216、FP-BM-DG-0252、FP-BM-SR-0061、FP-BM-SR-0013、FP-BM-MD-0026、FP-BM-MH-0051、FP-BM-TV-0136、FP-BM-TV-0128、FP-BM-MH-0023、FP-BM-DG-0208、FP-BM-MH-0063、FP-BM-MD-0058、FP-BM-MS-0024、FP-BM-DG-0240、FP-BM-MH-0015、FP-BM-MH-0019、FP-BM-TV-0140、FP-BM-TV-0164、FP-BM-SR-0025、FP-BM-MH-0039、FP-BM-MS-0048、FP-BM-MH-0027、FP-BM-MD-0002、FP-BM-SR-0021、FP-BM-DG-0228、FP-BM-TV-0120、FP-BM-MH-0047、FP-BM-MS-0056、FP-BM-MH-0003、FP-BM-MS-0064、FP-BM-SR-0041、FP-BM-MS-0008、FP-BM-TV-0144、FP-BM-TV-0124、FP-BM-MH-0011、FP-BM-MD-0010、FP-BM-MH-0035、FP-BM-TV-0116、FP-BM-MD-0054、FP-BM-TV-0156、FP-BM-TV-0148、FP-BM-MS-0020、FP-BM-MD-0014、FP-BM-DG-0248、FP-BM-DG-0244、FP-BM-MS-0032、FP-BM-MS-0052、FP-BM-MH-0043、FP-BM-SR-0017、FP-BM-MS-0040、FP-BM-SR-0001、FP-BM-TV-0152、FP-BM-DG-0220、FP-BM-MS-0028、FP-BM-DG-0212、FP-BM-MS-0012、FP-BM-SR-0057、FP-BM-SR-0053、FP-BM-MS-0004、FP-BM-MS-0016、FP-BM-MH-0059、FP-BM-MH-0031、FP-BM-DG-0224、FP-BM-MD-0006

未覆盖全部必需类别的发问题：

- FP-BM-TV-0108：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0104：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0060：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0204：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0160：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-MS-0044：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0055：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0036：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0232：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0007：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0236：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0132：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0216：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0252：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0051：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0136：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0128：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0023：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0208：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0063：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0024：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0240：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0015：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0019：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0140：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0164：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0039：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0048：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0027：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0228：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0120：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0047：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0056：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0003：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0064：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0008：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0144：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0124：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0011：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0035：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0116：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0156：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0148：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0020：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0248：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0244：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0032：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0052：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0043：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0040：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0152：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0220：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0028：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0212：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-MS-0012：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0004：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0016：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0059：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0031：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0224：缺 goal、occurrence_time、symptom_details

## C

- 会话 96，匹配 96，跳过 0
- 多轮发问题数 58；整题全覆盖 0/58；类别级覆盖 108/313
- 重复问 10 处
- 等待轮 101（多轮 79；其中多轮最终轮 8），带结论 0，不带结论 101
- 单轮等待轮数 22（不计入覆盖分母）

| 类别 | 需要该类的发问题数 | 已覆盖 | 缺失 |
| --- | ---: | ---: | ---: |
| account_context | 58 | 58 | 0 |
| api_version | 23 | 0 | 23 |
| goal | 58 | 0 | 58 |
| identifiers | 58 | 50 | 8 |
| occurrence_time | 58 | 0 | 58 |
| symptom_details | 58 | 0 | 58 |

重复问明细：

- FP-BM-MH-0015：identifiers
- FP-BM-MS-0032：identifiers
- FP-BM-MH-0023：account_context
- FP-BM-MH-0023：identifiers
- FP-BM-MS-0012：identifiers
- FP-BM-MH-0039：identifiers
- FP-BM-MH-0011：identifiers
- FP-BM-MH-0031：identifiers
- FP-BM-MS-0024：identifiers
- FP-BM-MS-0016：identifiers

等待轮不带结论的题（含单轮）：FP-BM-SR-0037、FP-BM-SR-0025、FP-BM-MH-0015、FP-BM-MD-0002、FP-BM-TV-0156、FP-BM-MH-0051、FP-BM-MS-0048、FP-BM-MS-0044、FP-BM-SR-0021、FP-BM-MS-0028、FP-BM-MH-0003、FP-BM-TV-0124、FP-BM-DG-0204、FP-BM-MS-0032、FP-BM-MD-0058、FP-BM-MS-0020、FP-BM-SR-0057、FP-BM-TV-0152、FP-BM-MS-0004、FP-BM-DG-0216、FP-BM-TV-0116、FP-BM-MH-0027、FP-BM-MH-0055、FP-BM-MD-0034、FP-BM-MD-0050、FP-BM-MD-0014、FP-BM-MD-0010、FP-BM-MD-0042、FP-BM-MS-0052、FP-BM-TV-0160、FP-BM-MS-0056、FP-BM-TV-0132、FP-BM-TV-0108、FP-BM-MH-0047、FP-BM-TV-0112、FP-BM-MH-0023、FP-BM-TV-0136、FP-BM-SR-0005、FP-BM-MS-0012、FP-BM-MH-0039、FP-BM-MH-0011、FP-BM-MH-0007、FP-BM-TV-0120、FP-BM-DG-0232、FP-BM-MD-0026、FP-BM-SR-0029、FP-BM-MH-0035、FP-BM-MS-0036、FP-BM-DG-0240、FP-BM-TV-0128、FP-BM-MD-0054、FP-BM-MD-0006、FP-BM-TV-0144、FP-BM-DG-0228、FP-BM-SR-0053、FP-BM-TV-0140、FP-BM-DG-0236、FP-BM-SR-0001、FP-BM-MH-0019、FP-BM-MS-0064、FP-BM-DG-0224、FP-BM-MH-0031、FP-BM-MD-0018、FP-BM-DG-0244、FP-BM-MH-0043、FP-BM-DG-0212、FP-BM-TV-0164、FP-BM-MS-0008、FP-BM-MD-0062、FP-BM-SR-0017、FP-BM-DG-0220、FP-BM-MS-0060、FP-BM-MH-0063、FP-BM-MS-0024、FP-BM-TV-0104、FP-BM-MS-0040、FP-BM-SR-0013、FP-BM-MS-0016、FP-BM-MH-0059、FP-BM-TV-0148

未覆盖全部必需类别的发问题：

- FP-BM-MH-0015：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0156：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0051：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0048：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0044：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0028：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0003：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0124：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0204：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0032：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0020：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0152：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0004：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0216：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0116：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0027：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0055：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0052：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0160：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-MS-0056：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0132：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0108：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0047：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0112：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0023：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0136：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0012：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0039：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0011：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0007：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0120：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0232：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0035：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0036：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0240：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0128：缺 api_version、goal、identifiers、occurrence_time、symptom_details
- FP-BM-TV-0144：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0228：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0140：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-DG-0236：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-MH-0019：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0064：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0224：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0031：缺 goal、occurrence_time、symptom_details
- FP-BM-DG-0244：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0043：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0212：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0164：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0008：缺 goal、identifiers、occurrence_time、symptom_details
- FP-BM-DG-0220：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0060：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MH-0063：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0024：缺 goal、occurrence_time、symptom_details
- FP-BM-TV-0104：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-MS-0040：缺 goal、occurrence_time、symptom_details
- FP-BM-MS-0016：缺 goal、occurrence_time、symptom_details
- FP-BM-MH-0059：缺 api_version、goal、occurrence_time、symptom_details
- FP-BM-TV-0148：缺 api_version、goal、occurrence_time、symptom_details

