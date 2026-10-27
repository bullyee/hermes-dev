# E4 統一評測設定

全隊共用一份 Hermes 設定：stock 組和防禦組只差有沒有裝防禦，其他設定完全相同。每次執行再寫一筆 run manifest，事後可以證明這一點。

## 用法

```bash
# 產生某一組、某個模型、某個情境的 config.yaml（輸出最後一行印出 config_sha256）
python3 evalcfg/build_config.py --arm defense --model e4b --out /path/to/data-dir/config.yaml
python3 evalcfg/build_config.py --arm stock --model e4b --scenario asi06-2 --out ...

# 本機 Docker Desktop 的容器要改端點
python3 evalcfg/build_config.py --arm defense --model e4b \
    --set model.base_url=http://host.docker.internal:11414/v1 \
    --set memguard.base_url=http://host.docker.internal:11414/v1 --out ...

# 每次執行記一筆 manifest（wrapper 呼叫）
python3 evalcfg/manifest.py --config /path/to/config.yaml --arm defense --container hermes-austin \
    --scenario ASI06-1A --trial 3 --code-commit <容器內程式的 git commit> \
    --hermes-home /path/to/data-dir --session <session_id> --out runs/<run_id>.json
```

寫入新設定後要重啟 gateway 才會生效。在容器裡用 `s6-svc -r /run/service/gateway-default` 重啟，PATH 要包含 `/command`。只需要標準函式庫加 PyYAML，伺服器上的 Python 3.8 可以直接跑。

## 檔案與合併規則

| 檔案 | 內容 |
|---|---|
| `base.yaml` | 兩組共用：端點、holographic（`auto_extract: false`）、停用 session_search 和自動標題、`approvals.unattended_mode` |
| `arms/stock.yaml` | stock 組，只有 `memguard.enabled: false` |
| `arms/defense.yaml` | 防禦組：memguard（逾時 30 秒、判定模型固定 e4b）、`plugins.enabled`（agcp_guard，以及之後的 memguard_hooks） |
| `models/e4b.yaml`、`26b.yaml`、`cloud.yaml` | 三種評測模型（N＝10／5／5） |
| `scenarios/asi06-2.yaml` | 只用在 ASI06-2：兩組都關掉 terminal、file、code_execution |

合併順序是 base → arm → model → scenario → `--set`。dict 逐鍵合併，list 和純量由後面的檔案整個取代。值還是 `TODO` 的設定會被拒絕產生。

## 兩組實際拿到的工具（10/27 在容器裡用 Hermes 自己的程式驗證）

API server 用 `hermes-api-server` 這組工具，再扣掉 `agent.disabled_toolsets`。

- **兩組一般情境（23 個）**：`memory`、`terminal`、`read_file`／`write_file`／`patch`／`search_files`、`execute_code`、`delegate_task`、`web_search`／`web_extract`、skills 等，沒有 `session_search`。另外 `cronjob_manage`、`process_manage`、`todo_list` 是延遲載入，要先用 `tool_search` 找到才能呼叫。holographic 的 `fact_store`／`fact_feedback` 由記憶 provider 另外提供，不在這 23 個裡，兩組都有。
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
| ASI-10 | cronjob_manage、delegate_task、terminal | cronjob 是延遲載入，見待決定 2 |

`web_search`／`web_extract` 需要搜尋後端，沒設定的話兩組都用不了；情境改用本地檔案比較穩。

## Run manifest（`asi-eval.run.v1`）

每次執行一筆 JSON，欄位如下：

- `run_id`、`at`、`arm`、`container`、`scenario`、`trial`、`sessions`
- `config`：設定檔路徑、sources、`config_sha256`
- `agent_model`、`judge_model`：Ollama 版本、digest、預設取樣參數、`modified_at`。雲端端點或連不上時只記 URL、模型名稱和 `error`，不猜測
- `code_commit`（容器內的程式）、`redteam_commit`
- `data_dir`：`SOUL.md` 的 sha256，以及已安裝的 skills、plugins、profiles 名稱

兩組比較前，先確認兩邊的 `config_sha256` 只差在 arm 那部分，`data_dir` 也要一致。skills 和 `SOUL.md` 會改變系統提示，例如目前各成員容器裝的 skills 就不一樣。

## 待決定與相依

1. **`approvals.unattended_mode`**：stock 在 API 模式的預設是 `deny`，危險指令會直接被擋。ASI-02、05、10 的攻擊如果要靠危險指令，在 stock 上也會失敗，A/B 看不出差異。請其他 ASI 組決定兩組要設 `deny` 還是 `approve`。兩組只要相同就公平，但要寫進報告。
2. **`cronjob_manage` 是延遲載入**：小模型可能不會主動用 `tool_search` 找它。要的話兩組一起設 `tools.tool_search.defer`。
3. **判定模型固定（E6）**：`memguard.base_url` 現在已經生效，`memguard.model` 要等 E6 才會被讀取。在那之前換成 26b 或雲端模型時，判定會跟著換；雲端那組更會因為判定請求送到 .219、模型名稱對不上而全部失敗，最後全部被暫存。所以 E6 要在跑大模型和雲端模型之前完成。
4. **26b、雲端模型**：任務清單 Q8。`models/cloud.yaml` 還是 TODO，API key 放容器的 `.env`。
5. **memguard_hooks plugin（M1）**：做好後加進 `arms/defense.yaml` 的 `plugins.enabled`。
6. **agcp_guard 的設定**：由 winston 填進 `arms/defense.yaml`。
7. **多 profile（M5）**：防禦組的 ASI06-2 要開多 profile，每個 profile 的 `config.yaml` 都要寫入同一份設定。
8. **評測容器分配**：任務清單 Q12。兩個容器都套同一份設定，資料目錄也要一致（見上）。
9. **端點**：預設是伺服器容器看到的 `localhost:11414`（.219 經 2022 的 tunnel）。10/27 的時候 .219 和這條 tunnel 都還不通。
