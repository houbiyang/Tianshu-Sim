# GenTactix-DSH

[简体中文](README.md) | **English**

A desktop workspace for historical campaign simulation and decision review. Built with Vue 3, Electron, FastAPI and DeepSeek Harness (DSH), it brings historical sources, map-based state, turn progression and decision traces together for teaching, scenario discussion and software research.

![Campaign selection](docs/screenshots/launch.png)

## Features

- **Campaigns and scenarios:** start with a map of China and its surroundings. The current source catalog includes Changping, nine Korean War campaigns, 15 Long March scenarios and 20 WWII China scenarios. You can also create a scenario through a guided conversation.
- **Map and state:** display units, movement trails and status. When data is available, show terrain features, fields of fire, weapon ranges, supply nodes and routes, and personnel/equipment losses. Missing values remain unknown.
- **Replayable local exercises:** 20 WWII China entries offer fixed initial states and seven teaching rule types for orders, personnel/equipment ledgers, inventory and objectives. Command counterfactuals reuse the same pre-state. These are synthetic exercises, not historically calibrated deployments; see the [design and limits](docs/可重放推演设计.md) (Chinese).
- **Turn progression:** select the turn duration and let the application maintain the clock. State checks preserve the roster and flag missing units, invalid coordinates and numeric anomalies. Optional geometric adjudication constrains movement, supply connectivity and encirclement.
- **DSH agents and plugins:** Native tool mode supports persistent role sessions, read-only domain tools, runtime events, budgets and in-flight cancellation. An application-level JSON tool mode remains available for compatibility.
- **Decision review:** inspect reports, key decisions and turning points; use traces for attribution analysis. Geometric counterfactuals and paired model branches leave the main campaign state intact.
- **Sources and saves:** local historical retrieval, campaign-specific adjudication parameters, checkpoints, recovery and JSON import/export.
- **Model settings:** configure vLLM / OpenAI-compatible or DeepSeek endpoints, models, credentials, timeouts and role budgets through the menu. Settings are stored encrypted.

Editable research packages: [Long March](data/long_march/README.md) and [WWII China](data/anti_japanese_war/README.md), documented in Chinese. Cards retain sources, conflicting accounts and unknown values; new geometry presets are teaching estimates. Previously installed releases require a rebuild to include these additions.

![Workspace tested with fixture responses](docs/screenshots/workspace.png)

## Campaign and scenario catalog

The current source catalog contains **45 entries**: Changping (1), the Long March (15), WWII China (20), and the Korean War (9). Each linked card contains forces, events, sources and disputed data, primarily in Chinese. Dates retain the precision used in the catalog. Overviews, stages and constituent battles are separate entries; their forces and results must not be added together.

| Mode | Purpose | Current availability |
| --- | --- | --- |
| Open simulation | Explore alternative decisions and model-generated developments in a historical setting | All 45 entries; model-generated initial state with optional geometric adjudication |
| Historical replay | Explain the recorded sequence and decisions using sources | All 45 entries; historical casualty totals are not automatically deducted from units |
| Fixed local exercise | Compare orders from the same initial state and check ledgers, supply and executable objectives | One synthetic exercise for each of the 20 WWII China entries; available in open mode only |

Fixed exercises use six teaching formations, 30-minute turns and at most four turns, across seven rule types. Personnel, equipment, local positions and coefficients are teaching assumptions rather than full historical deployments. “Local rules” below identifies the implemented exercise type; other teaching topics do not imply that every mechanism is implemented. State does not automatically carry over between campaigns.

### Ancient warfare: Changping (1 entry)

The Qin–Zhao struggle over Shangdang, centered on the decisive operations of 260 BCE, introduces defensive positions, deception, force separation and supply interdiction.

| Scenario | Period | Introduction and teaching topics |
| --- | --- | --- |
| [Changping](corpus/长平之战.md) | 262–260 BCE | Operations around modern Gaoping, Shanxi; explore command changes, pursuit, flanking and supply routes. The card distinguishes figures in the *Records of the Grand Historian* from modern debate; HP is not a historical headcount. |

### Long March (15 entries)

Organized by strategic movements and key nodes, covering the Central Red Army, Fourth Front Army, Second and Sixth Corps, and 25th Army on parallel tracks, plus consolidation after arrival in northern Shaanxi. These are not 15 consecutive battles fought by one force.

| ID and scenario | Catalog period | Introduction and teaching topics |
| --- | --- | --- |
| LM01 · [Xiang River campaign](corpus/红军长征-湘江战役-1934.11.25至1934.12.01.md) | 1934-11-25 – 1934-12-01 | Crossings and blocking actions around Quanzhou, Xing’an and Guanyang; explore baggage delays, crossing capacity and the time bought by covering forces. |
| LM02 · [Crossing the Wu River](corpus/红军长征-强渡乌江-1935.01.01至1935.01.06.md) | 1935-01-01 – 1935-01-06 | Operations at Jiangjiehe and other crossings; explore advance parties, bridge construction and the passage of follow-on formations. |
| LM03 · [Four crossings of the Chishui](corpus/红军长征-四渡赤水-1935.01.19至1935.03.22.md) | 1935-01-19 – 1935-03-22 | Maneuver through Tucheng, Zhaxi, Zunyi and Chishui crossings; explore intelligence, route changes and escape from encirclement. |
| LM04 · [Central Red Army crossing of the Jinsha](corpus/红军长征-中央红军巧渡金沙江-1935.05.03至1935.05.09.md) | 1935-05-03 – 1935-05-09 | The Central Red Army’s crossings around Jiaopingdu; explore boat capacity, crossing selection and concentration of the main force. |
| LM05 · [Dadu crossing and Luding Bridge](corpus/红军长征-强渡大渡河与飞夺泸定桥-1935.05.24至1935.05.29.md) | 1935-05-24 – 1935-05-29 | Linked actions at Anshunchang and Luding Bridge; explore ferry bottlenecks, split routes and the deadline for capturing a bridge. |
| LM06 · [Maogong link-up and mountain/grassland movements](corpus/红军长征-懋功会师与雪山草地转移-1935.06.12至1935.09.12.md) | 1935-06-12 – 1935-09-12 | The Dawei and Maogong link-ups followed by mountain and grassland movements; explore separate command chains, route disputes and noncombat losses. |
| LM07 · [Baozuo campaign](corpus/红军长征-包座战役-1935.08.29至1935.09.01.md) | 1935-08-29 – 1935-09-01 | Operations around Qiuji Temple, Dajin Temple and neighboring valleys; explore the allocation of forces between siege and interception of relief troops. |
| LM08 · [Lazikou battle](corpus/红军长征-腊子口战斗-1935.09.16至1935.09.17.md) | 1935-09-16 – 1935-09-17 | A narrow-pass assault combining frontal pressure with a cliff-flanking movement; explore terrain, concealed approach and coordination. |
| LM09 · [25th Red Army’s Long March](corpus/红军长征-红二十五军长征-1934.11.16至1935.09.16.md) | 1934-11-16 – 1935-09-16 | An independent campaign through Dushuzhen, Yujiahe, the Hubei–Henan–Shaanxi base area and northern Shaanxi; explore movement and base building. |
| LM10 · [Jialing River campaign](corpus/红军长征-嘉陵江战役-1935.03.28至1935.04.21.md) | 1935-03-28 – 1935-04-21 | The Fourth Front Army’s Jialing crossing and advance toward Jianmen Pass; explore multiple crossing points and bridgehead expansion. |
| LM11 · [Baizhang Pass campaign](corpus/红军长征-百丈关战役-1935.11.md) | 1935-11 | A turning point in the Fourth Front Army’s southward operations; explore an exposed advance, counterattacks and endurance; conflicting dates remain documented. |
| LM12 · [Wumeng Mountain maneuvers](corpus/红军长征-乌蒙山回旋战-1936.02.27至1936.03.28.md) | 1936-02-27 – 1936-03-28 | The Second and Sixth Corps’ maneuvers along the Guizhou–Yunnan border; explore route choices, imperfect intelligence and evasion. |
| LM13 · [Second and Sixth Corps’ Jinsha crossing](corpus/红军长征-红二六军团抢渡金沙江-1936.04.25至1936.04.28.md) | 1936-04-25 – 1936-04-28 | The Second and Sixth Corps cross near Shigu in 1936; explore crossing coordination and distinguish this operation from the Central Red Army’s 1935 crossing. |
| LM14 · [Zhiluozhen campaign](corpus/红军长征-直罗镇战役-1935.11.20至1935.11.24.md) | 1935-11-20 – 1935-11-24 | A consolidation battle after the Central Red Army reached northern Shaanxi; explore a valley ambush, encirclement and relief interception. |
| LM15 · [Ganzi to the three main forces’ link-ups](corpus/红军长征-甘孜会师至三大主力会师-1936.07.01至1936.10.22.md) | 1936-07-01 – 1936-10-22 | From concentration and reorganization at Ganzi to the Huining and Jiangtaibao link-ups; explore converging routes, support and timing. |

See the [Long March data guide](data/long_march/README.md) and [file index](data/long_march/INDEX.md). The 1935 Jiaopingdu and 1936 Shigu crossings have separate cards; Zhiluozhen is a post-arrival consolidation scenario.

### WWII China (20 entries)

Organized by strategic stage and theater: conventional fronts, operations behind Japanese lines, and the cross-border southwestern theater. The three Changsha campaigns have separate cards. Pingxingguan, Tai’erzhuang, Wanjialing, Hengyang and Cheqiao link to parent overviews. Local Chinese counteroffensives and Japanese strategic offensives could coexist in 1944; the year alone does not determine a scenario’s posture.

| ID and scenario (catalog period) | Introduction and teaching topics | Local rules |
| --- | --- | --- |
| AJ01 · [Shanghai campaign](corpus/抗日战争-淞沪会战-1937.08.13至1937.11.12.md)<br>1937-08-13 – 1937-11-12 | Shanghai’s urban districts, Wusong–Luodian and Hangzhou Bay; explore landing threats, reserve allocation and withdrawal timing. | Delay |
| AJ02 · [Taiyuan campaign](corpus/抗日战争-太原会战-1937.09至1937.11.md)<br>1937-09 – 1937-11 | Defense from Pingxingguan through Xinkou and Niangziguan to Taiyuan; explore coordination between the front and raids on rear communications. | Delay |
| AJ03 · [Pingxingguan ambush](corpus/抗日战争-平型关伏击战-1937.09.25.md)<br>1937-09-25 | The 115th Division’s road ambush at Qiaogou; explore concealment, column isolation and withdrawal, separately from the wider pass defense. | Assault |
| AJ04 · [Xuzhou campaign overview](corpus/抗日战争-徐州会战总览-1938.01至1938.05.md)<br>1938-01 – 1938-05 | A multi-direction campaign across northern Jiangsu, southern Shandong and the Xuzhou railway hub; explore linked fronts and withdrawal before encirclement. | Breakout |
| AJ05 · [Tai’erzhuang battle](corpus/抗日战争-台儿庄战役-1938.03.23至1938.04.07.md)<br>1938-03-23 – 1938-04-07 | Urban defense and attacks from outside Tai’erzhuang; explore how long defenders must hold for surrounding forces to act. | Assault |
| AJ06 · [Wuhan campaign](corpus/抗日战争-武汉会战-1938.06至1938.10.md)<br>1938-06 – 1938-10 | A broad delaying campaign along the Yangtze and across four provinces; explore forts, transport and strategic evacuation. | Delay |
| AJ07 · [Wanjialing battle](corpus/抗日战争-万家岭战役-1938.09下旬至1938.10.md)<br>1938-09 (late) – 1938-10 | A local mountain encirclement within the Wuhan campaign; explore containment, relief interception and the requirements for eliminating an encircled force. | Assault |
| AJ08 · [First Changsha campaign](corpus/抗日战争-第一次长沙会战-1939.09.14至1939.10.md)<br>1939-09-14 – 1939-10 | Three approaches through northern Hunan, southern Hubei and northern Jiangxi; explore successive defense, flank attacks and supply. | Delay |
| AJ09 · [Second Changsha campaign](corpus/抗日战争-第二次长沙会战-1941.09.07至1941.10.09.md)<br>1941-09-07 – 1941-10-09 | Operations from northern Hunan toward Changsha and Zhuzhou; explore reorganization and counterattack after a breached defense. | Breakout |
| AJ10 · [Third Changsha campaign](corpus/抗日战争-第三次长沙会战-1941.12.24至1942.01.16.md)<br>1941-12-24 – 1942-01-16 | Changsha’s defense and operations on the outer flanks; explore the timing of delay, city defense and encirclement. | Assault |
| AJ11 · [Southern Guangxi and Kunlun Pass](corpus/抗日战争-桂南会战与昆仑关攻坚-1939.11至1940.01.md)<br>1939-11 – 1940-01 | Southern Guangxi’s transport routes and the Kunlun Pass counteroffensive; explore mountain strongpoints, heights and reinforcement routes. | Assault |
| AJ12 · [Ichi-Go overview (Henan–Hunan–Guangxi)](corpus/抗日战争-豫湘桂战役总览-1944.04至1944.12.md)<br>1944-04 – 1944-12 | The Henan, Changsha–Hengyang and Guangxi phases of Operation Ichi-Go; explore command coordination, railways and effective strength. | Breakout |
| AJ13 · [Defense of Hengyang](corpus/抗日战争-衡阳保卫战-1944.06至1944.08.08.md)<br>1944-06 – 1944-08-08 | An isolated city defense within Ichi-Go; explore fortifications, stock depletion, relief expectations and withdrawal choices. | Delay |
| AJ14 · [Hundred Regiments Offensive](corpus/抗日战争-百团大战-1940.08.20至1941.01.24.md)<br>1940-08-20 – 1941-01-24 | Disruption of the Zhengtai and other transport routes, followed by counter-sweep operations; explore damage, repair and pressure on base areas. | Transport disruption |
| AJ15 · [Central Hebei May Day counter-sweep](corpus/抗日战争-冀中五一反扫荡-1942.05.01至1942.06.20.md)<br>1942-05-01 – 1942-06-20 | Movement and survival during the major sweep in central Hebei; explore concealment, organizational continuity and breakout. | Concealed survival |
| AJ16 · [1944 local counteroffensives overview](corpus/抗日战争-敌后局部反攻总览-1944.md)<br>1944 | Parallel local offensives across several base areas in 1944; explore target selection and concentration without double-counting annual totals. | Assault |
| AJ17 · [Cheqiao battle](corpus/抗日战争-车桥战役-1944.03.05至1944.03.06.md)<br>1944-03-05 – 1944-03-06 | Assault on Cheqiao and interception of relief forces near Lujiatan; explore strongpoint capture and links between base areas. | Assault |
| AJ18 · [Western Hunan campaign](corpus/抗日战争-湘西会战-1945.04.09至1945.06.07.md)<br>1945-04-09 – 1945-06-07 | Defense and counterattack between the Xuefeng Mountains and Zhijiang; explore mountain blocking actions, air support and Japanese logistics. | Air support and supply arrivals |
| AJ19 · [Northern Burma–western Yunnan overview](corpus/抗日战争-缅北滇西反攻总览-1943.10至1945.03.md)<br>1943-10 – 1945-03 | Parallel offensives in northern Burma and western Yunnan; explore cross-border coordination, strongpoints and restoration of international supply routes. | Air support and supply arrivals |
| AJ20 · [General counteroffensive and surrender reception](corpus/抗日战争-全面反攻与受降总览-1945.08至1945.09.md)<br>1945-08 – 1945-09 | Offensives and reception in August–September 1945; distinguish surrender announcements, ceasefires, disarmament and formal signing. | Ceasefire reception |

See the [WWII China data guide](data/anti_japanese_war/README.md) and [scenario/stage matrix](data/anti_japanese_war/INDEX.md) for sources and parent relationships. The [replayable exercise design](docs/可重放推演设计.md) describes objectives and settlement rules; the [per-scenario acceptance report](docs/局部推演规则与逐场验收-2026-10-22.md) records live results and limits. These are selected teaching entries, not a complete list of WWII China battles.

### Korean War (9 entries)

The catalog groups the first five maneuver campaigns and four later operations. The second campaign distinguishes western and eastern fronts; the 1951 summer–autumn defense is one entry. The 1953 summer counteroffensive includes Kumsong as a subphase: its alias selects the overall card, so specify the July phase in the scenario.

| Scenario | Catalog period | Introduction and teaching topics |
| --- | --- | --- |
| [First campaign](corpus/抗美援朝-第一次战役-1950.10.25至1950.11.05.md) | 1950-10-25 – 1950-11-05 | Encounter battles and counterattacks around Onjong, Unsan and Huangcaoling; explore concealed approach, dispersed forces and blocking actions. |
| [Second campaign](corpus/抗美援朝-第二次战役-1950.11.07至1950.12.24.md) | 1950-11-07 – 1950-12-24 | Counteroffensives on the Chongchon and Chosin fronts; explore deception, maneuver, supply distance and cold-related losses. |
| [Third campaign](corpus/抗美援朝-第三次战役-1950.12.31至1951.01.08.md) | 1950-12-31 – 1951-01-08 | Advance across the 38th parallel toward Seoul and the Han River; explore breakthrough tempo, pursuit and extended supply lines. |
| [Fourth campaign](corpus/抗美援朝-第四次战役-1951.01.25至1951.04.21.md) | 1951-01-25 – 1951-04-21 | Defense and counterattacks around the Han River, Hoengsong and Chipyong-ni; explore trading space for time and concentrating reinforcements. |
| [Fifth campaign](corpus/抗美援朝-第五次战役-1951.04.22至1951.06.10.md) | 1951-04-22 – 1951-06-10 | A phased offensive followed by withdrawal and blocking actions; explore culmination, stock depletion, communications and pursuit. |
| [Summer–autumn defense, 1951](corpus/抗美援朝-夏秋季防御战役-1951.08.18至1951.10.22.md) | 1951-08-18 – 1951-10-22 | Strongpoint defense under air interdiction in summer–autumn 1951; explore fortifications, antitank actions and supply pressure. |
| [Autumn tactical counterattacks, 1952](corpus/抗美援朝-秋季战术反击作战-1952.09.18至1952.10.31.md) | 1952-09-18 – 1952-10-31 | Limited attacks at multiple points in autumn 1952; explore artillery support, short assaults and the difference between capture and retention. |
| [Shangganling battle](corpus/抗美援朝-上甘岭战役-1952.10.14至1952.11.25.md) | 1952-10-14 – 1952-11-25 | Fighting for Hill 597.9 and the northern part of Hill 537.7; explore surface positions, tunnel survival, supply and repeated counterattacks. |
| [Summer counteroffensive including Kumsong](corpus/抗美援朝-夏季反击战役含金城战役-1953.05.13至1953.07.27.md) | 1953-05-13 – 1953-07-27 | The phased 1953 counteroffensive, including Kumsong in July; explore fire preparation, exploitation and the ceasefire deadline. |

The second campaign’s catalog start includes preparation; the western and eastern counteroffensives began on November 25 and 27 respectively. See the [Korean War source guide](docs/抗美援朝资料说明.md) for date variants, statistical scope and geographic references. The Korean War and Long March entries do not yet use the fixed local exercises above; cold injuries, layered tunnel control, negotiations and cross-campaign inheritance remain modeling topics in the source cards.

### Custom scenarios

Use the startup conversation to create a scenario, optionally linked to an existing card. An independent scenario has no pre-researched dedicated archive: supply forces, initial conditions, geography and objectives, and review model-generated content. See the [source-card format](corpus/README.md) and [adjudication guide](adjudication/README.md) to add catalog entries.

## Install and launch

Current desktop release: [v3.2.1](https://github.com/snow-wind-001/Gentactix-DSH/releases/tag/v3.2.1). See the [release notes](docs/releases/v3.2.1.md) for the DeepSeek endpoint fix, bundled DSH runtime and installation validation.

Download a Linux x86_64 package from [Releases](https://github.com/snow-wind-001/Gentactix-DSH/releases). Packages include the backend and DSH runtime; a separate Python or Conda installation is unnecessary. Simulations still require a working model service.

| Package | Usage |
| --- | --- |
| Ubuntu `.deb` | Install with the system installer or `sudo apt install ./GenTactix-DSH-<version>-amd64.deb`, then find **GenTactix** in the applications menu |
| `.AppImage` | Make executable and double-click; use `APPIMAGE_EXTRACT_AND_RUN=1` when FUSE is unavailable |
| `.tar.gz` | Extract and run `gentactix-dsh` |

From a source checkout, install an AppImage with a per-user menu entry and desktop icon:

```bash
python3 tools/install_linux.py release/GenTactix-DSH-3.2.1-x86_64.AppImage --project-root .
```

See the [desktop guide (Chinese)](docs/桌面启动与配置.md) for packaging and configuration migration. Linux packages are provided; Windows and macOS packages have not been validated.

## Run a scenario

1. Open **配置 → DSH · LLM配置** (`Ctrl+,`), configure the endpoint and model, save, and test the connection. For vLLM, the model ID must match the server's `served-model-name`.
2. Choose a campaign or create a scenario through the startup conversation. Select open simulation or historical replay, then review the turn duration, retrieval and adjudication settings. WWII China entries also offer a fixed local exercise; its initialization requires no model call.
3. Advance turns, inspect the map and reports, ask questions, review decisions or compare counterfactual branches.
4. Export the campaign as JSON or restore a saved session from the startup screen.

Settings use Fernet authenticated encryption in `.gentactix-llm.enc` in the project directory. The decryption key is stored separately in the user's configuration directory. If the installation directory is read-only, the desktop app uses a writable project directory under user data. Public source and packages contain no personal API key. A user with access to both the ciphertext and key can still decrypt the settings.

## Run from source

Use Python 3.10+, Node.js 22 and a graphical desktop. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dsh.txt
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700
```

In another terminal, start the Electron development interface:

```bash
cd frontend
npm ci
npm run dev
```

The original Streamlit interface remains in `app.py`; launch it with `.venv/bin/streamlit run app.py`. Its feature set differs from the desktop application.

## Repository layout

| Path | Contents |
| --- | --- |
| `frontend/` | Vue interface, map and Electron entry point |
| `backend/` | FastAPI endpoints, sessions and analysis services |
| `gentactix/` | Simulation, state preservation, clock, geography, retrieval and adjudication |
| `plugins/dsh-gentactix/` | DSH native domain tools, runtime observation and control |
| `corpus/`, `adjudication/` | Historical source cards and campaign parameters |
| `verify_*.py`, `tools/` | Regression checks, live model probes, build and installation tools |

## Validation and limits

```bash
.venv/bin/python verify_all.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

See [TESTING.md](TESTING.md) for coverage. The [50-turn audit (Chinese)](docs/50步推演与输出审计-2026-10-22.md) preserves live failures and semantic issues. The [3.1.3 repair report (Chinese)](docs/补给射界与战报一致性修复-2026-10-22.md) covers supply, firing geometry and report validation, with historical replay, four live continuation turns and review checks. The [3.1.4 recovery report (Chinese)](docs/空正文恢复修复-2026-10-22.md) covers one bounded retry for completed runs without final text, cancellation and state preservation, 139 regression tests and a live continuation from a copied session. These tests do not establish correctness across all campaigns or model behaviors.

This is a teaching and interpretation aid. Model-generated casualties, morale and attribution are not validated combat-effectiveness estimates or causal findings; HP is not a personnel count. Geometric adjudication is off by default and does not replace ballistics, terrain occlusion or logistics throughput models. Fields of fire are illustrative overlays. The offline map provides coarse outlines; online tiles require a network connection. See the [architecture notes (Chinese)](docs/因果分析与DSH插件架构.md) for DSH integration and counterfactual boundaries.

## Development and acknowledgements

**Developed and maintained by the School of Equipment Engineering, Shenyang Ligong University (沈阳理工大学装备工程学院).** Development team: Deep Learning Laboratory.

Based on [pystrategylab/Gentactix](https://github.com/pystrategylab/Gentactix). We thank its author for making the original project open source. Upstream copyright notices are retained under the [MIT license](LICENSE); see [NOTICE.md](NOTICE.md) for attribution.
