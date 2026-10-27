# E4 統一評測設定

全隊共用一份 Hermes 設定：stock 組和防禦組只差有沒有裝防禦，其他設定完全相同。每批開跑前用 `check_env.py` 確認容器真的套用了這份設定、連到的是預期的模型。

## 評測流程

一批是同一組、同一個模型、同一個情境連跑 N 次。在伺服器上、repo 根目錄執行，以防禦組、e4b 為例：

```bash
H=~/develop-agent-security/data-austin

# 1. 產生設定，直接寫進容器的資料目錄（容器裡的 /opt/data）。會整份蓋掉原本的 config.yaml，
#    沒寫到的設定回到 Hermes 預設；第一次蓋之前先備份，並跟容器的主人講好
python3 evalcfg/build_config.py --arm defense --model e4b --out $H/config.yaml

# 2. 重啟 gateway（Hermes 只在啟動時讀設定）
docker exec hermes-austin sh -c 'PATH=/command:$PATH s6-svc -r /run/service/gateway-default'

# 3. 開跑前檢查：全部 OK 才開始；最後一行 OK 貼進報告的環境表
python3 evalcfg/check_env.py --hermes-home $H --container hermes-austin --arm defense --model e4b
```

4. 跑 N 次攻擊。
5. 每次跑完先把證據從資料目錄複製出來，再重建資料目錄（E3 的 reset），才跑下一次（見待決定 1）。

`check_env.py` 檢查四件事，任何一項不對就印出 `FAIL` 並結束碼為 1：

| 檢查 | 抓到的問題 |
|---|---|
| 設定 | `config.yaml` 不是 `build_config.py` 為這組、這個模型、這個情境產生的（手改過、套錯組、overlay 之後改過）。多 profile 時用 `--profile <名稱>` 一起檢查 |
| 重啟 | 寫入設定之後 gateway 沒有重啟（比對 `gateway-starts.log`） |
| 模型 | agent 模型和防禦組的判定模型，digest 不是 `check_env.py` 裡 `DIGESTS` 的值（例如 tunnel 接到別台機器）；雲端模型沒有 digest，只印名稱 |
| plugin | `plugins.enabled` 列的 plugin 在容器裡沒有安裝或沒有啟用 |

個別設定可用 `--set` 覆蓋（值照 YAML 解析），覆蓋的內容會記在產生的檔頭，`check_env.py` 也會照檔頭比對：

```bash
python3 evalcfg/build_config.py --arm defense --model e4b --set memguard.timeout=45 --out $H/config.yaml
```

只需要標準函式庫加 PyYAML，伺服器上的 Python 3.8 可以直接跑。

## 檔案與合併規則

| 檔案 | 內容 |
|---|---|
| `base.yaml` | 兩組共用：端點、holographic（`auto_extract: false`）、停用 session_search 和自動標題、危險指令自動核准（`approvals.unattended_mode: approve`） |
| `arms/stock.yaml` | stock 組，只有 `memguard.enabled: false` |
| `arms/defense.yaml` | 防禦組：memguard（逾時 30 秒、判定模型固定 e4b）、`plugins.enabled`（agcp_guard，以及之後的 memguard_hooks） |
| `models/e4b.yaml`、`26b.yaml`、`cloud.yaml` | 三種評測模型（N＝10／5／5） |
| `check_env.py` | 開跑前檢查，以及兩個本地模型的預期 digest（`DIGESTS`） |
| `scenarios/asi06-2.yaml` | 只用在 ASI06-2：兩組都關掉 terminal、file、code_execution |

合併順序是 base → arm → model → scenario → `--set`。dict 逐鍵合併，list 和純量由後面的檔案整個取代。值還是 `TODO` 的設定會被拒絕產生。

## 兩組實際拿到的工具（10/27 在容器裡用 Hermes 自己的程式驗證）

API server 用 `hermes-api-server` 這組工具，再扣掉 `agent.disabled_toolsets`。

- **兩組一般情境（24 個）**：`memory`、`terminal`、`read_file`／`write_file`／`patch`／`search_files`、`execute_code`、`delegate_task`、`web_search`／`web_extract`、skills 等，沒有 `session_search`。`cronjob_manage` 已改成直接可用（見待決定 2）；`process_manage`、`todo_list` 維持延遲載入，要先用 `tool_search` 找到才能呼叫。holographic 的 `fact_store`／`fact_feedback` 由記憶 provider 另外提供，不在這 23 個裡，兩組都有。
- **ASI06-2（16 個，加上 fact_store／fact_feedback）**：再少掉 terminal、檔案工具和 `execute_code`。延遲目錄是從當下的工具清單建的（`tools/tool_search.py` 的設計），被關掉的工具不在裡面；但還沒有實際試過用 `tool_call` 去叫，M5 驗 ASI06-2 環境時要順便試。

## 各 ASI 需要的工具（草稿，請各組確認）

| 風險 | 需要的工具 | 備註 |
|---|---|---|
| ASI06-1、3、4、5 | memory、fact_store | 讀外部內容的情境另需 file 或 web |
| ASI06-2 | memory、fact_store | 用 `scenarios/asi06-2.yaml` |
| ASI06-6 | delegate_task | 子 agent 沿用父 agent 的工具 |
| ASI-01 | read_file、web | 間接注入的來源文件 |
| ASI-02 | terminal、file | 見待決定 1 |
| ASI-05 | terminal、execute_code | 見待決定 1 |
| ASI-09 | read_file | |
| ASI-10 | cronjob_manage、delegate_task、terminal | |

`web_search`／`web_extract` 需要搜尋後端，沒設定的話兩組都用不了；情境改用本地檔案比較穩。

## 報告的環境表

不必每次執行都記，報告裡寫一張表即可：

- 各批 `check_env.py` 的 OK 那一行：組別、模型、情境、設定 sha、模型 digest、Ollama 版本、取樣參數、判定模型、plugin
- 兩個容器部署的 Hermes commit（E5）和這份 `evalcfg/` 的 commit
- 紅隊的攻擊模型和評分模型（紅隊提供）

另外，兩個評測容器的 `SOUL.md` 和已安裝的 skills 會改變系統提示，正式評測前要確認兩邊相同（只需確認一次）。

## 待決定與相依

1. **危險指令自動核准（10/27 決定）**：stock 在 API 模式的預設是 `deny`，會讓 ASI-02、05、10 的攻擊在 stock 上也失敗，A/B 看不出差異。所以兩組都設 `approve`，擋不擋交給防禦，報告要寫明這是評測設定，不是 Hermes 的預設。代價是攻擊真的會在容器裡執行指令。容器只掛載自己的 `data-<name>`，但設定、state.db、記憶，以及 memguard 和藍隊的紀錄檔都在裡面，所以每次執行完要先把證據複製出來，下次執行前再重建資料目錄。
2. **`cronjob_manage` 改成直接可用（10/27 決定）**：Hermes 預設把它延遲載入，要透過 `tool_call` 呼叫。在 .219 的 e4b 上測 3 次，3 次都找到工具，但有 2 次直接用名字呼叫、被拒絕後就放棄，ASI-10 的排程攻擊會在 stock 上因為這個原因失敗。所以兩組都把它從 `tools.tool_search.defer` 拿掉，改成直接可用後 3 次都成功建立排程。
3. **判定模型固定（E6）**：`memguard.base_url` 現在已經生效，`memguard.model` 要等 E6 才會被讀取。在那之前換成 26b 或雲端模型時，判定會跟著換；雲端那組更會因為判定請求送到 .219、模型名稱對不上而全部失敗，最後全部被暫存。所以 E6 要在跑大模型和雲端模型之前完成。
4. **雲端模型**：任務清單 Q8。`models/cloud.yaml` 還是 TODO，API key 放容器的 `.env`。26b 已在 2022 那台（digest `001e5dafc3c7`）。
5. **memguard_hooks plugin（M1）**：做好後加進 `arms/defense.yaml` 的 `plugins.enabled`。
6. **agcp_guard 的設定**：由 winston 填進 `arms/defense.yaml`。
7. **多 profile（M5）**：防禦組的 ASI06-2 要開多 profile，每個 profile 的 `config.yaml` 都要寫入同一份設定。
8. **評測容器分配**：任務清單 Q12。兩個容器都套同一份設定，`SOUL.md` 和 skills 也要一致（見上）。
9. **端點**：預設是伺服器上的容器透過 11414 的 tunnel 連到 .219（2022 那台），跑評測前要確認伺服器上這條 tunnel 是開著的。
