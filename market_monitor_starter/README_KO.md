# GitHub Actions + yfinance + ChatGPT 미국 주식 모니터링 구축 가이드

이 프로젝트는 GitHub Actions에서 미국 정규장에만 QQQ·QQQM·VOO 데이터를 수집하고, **GitHub Pages 공개 JSON**으로 배포하는 개인 연구용 예제입니다. 주문이나 자동매매는 수행하지 않습니다.

> 먼저 확인: 이 프로젝트를 GitHub에 업로드했다고 ChatGPT에 자동 연결되는 것은 아닙니다. 공개 JSON URL을 ChatGPT가 실제로 조회할 수 있는지 확인한 뒤, **이미 존재하는** `QQQ·ETF 시간별 점검` 예약 작업에 URL을 추가하세요.

## 0. 필요한 것

- GitHub 계정 (https://github.com)
- 공개 저장소(무료 플랜 사용 가능)와 GitHub Actions/Pages 활성화
- 설치용 Python은 필수 아님. GitHub 서버가 실행합니다.
- Yahoo 계정/API 키는 보통 불필요. 단, `yfinance`는 비공식 공개 API 기반이므로 제한·장애·지연이 발생할 수 있습니다.

## 1. GitHub 저장소 만들기

1. GitHub에서 **New repository** 클릭.
2. 저장소 이름을 `market-etf-monitor`로 지정하고 **Public** 선택. `Add a README file`을 체크하여 `main` 브랜치를 생성해도 됩니다.
3. 이 ZIP 파일을 풀어 내부 파일을 저장소 최상위 폴더에 올립니다. 중요: `.github/workflows/market_monitor.yml`의 경로를 변경하지 마세요.
4. Windows에 Git이 설치되어 있다면 다음 순서도 가능합니다.

   ```powershell
   git clone https://github.com/사용자명/market-etf-monitor.git
   # 압축 해제한 ZIP 내부 파일들을 market-etf-monitor 폴더에 복사
   cd market-etf-monitor
   git add .
   git commit -m "Add hourly yfinance monitor"
   git push
   ```

5. GitHub에서 **Actions** 탭으로 이동한 뒤, 워크플로가 표시되었는지 확인합니다. 필요한 경우 `Enable workflows`를 클릭합니다.

## 2. GitHub Pages 켜기

- 저장소 **Settings → Pages → Build and deployment → Source → GitHub Actions** 선택.
- 이 예제는 최신 GitHub Pages 방식인 `actions/upload-pages-artifact` + `actions/deploy-pages`로 배포합니다. **Deploy from a branch를 선택하면 안 됩니다.**
- 배포에 사용할 개인 액세스 토큰(PAT)은 필요하지 않습니다. GitHub Actions의 일시적인 `GITHUB_TOKEN` 권한(`pages:write`, `id-token:write`)을 사용합니다.
- 참고: GitHub Pages는 **공개되는 주소**입니다. 계좌·평단·현금액·개인 식별정보·비밀번호는 데이터에 절대 넣지 마세요.

## 3. 지금 당장 배포 시험하기

1. **Actions → Hourly US ETF indicators → Run workflow**.
2. `manual_test`를 **true**로 유지하고 실행합니다. 장이 닫힌 주말에도 시험할 수 있습니다.
3. **collect**, **deploy** 두 작업이 성공하는지 확인합니다.
4. Settings → Pages에 표시되는 주소를 확인합니다. 주소 형태는 다음과 같습니다.

   ```text
   https://깃허브사용자명.github.io/market-etf-monitor/data/latest.json
   ```

5. 브라우저에서 JSON 파일이 열리면 1단계(공개 데이터 배포)는 성공입니다.
6. `mode` 값이 `manual_test_not_for_trading`인 시험 자료는 가격·시장 판단에 사용하지 마세요. 다음 실제 정규장 점검이 성공하면 `regular_session`으로 바뀝니다.

> **유의:** GitHub Actions 러너가 Yahoo Finance에 접속할 수 있어야 합니다. 야후가 일부 클라우드 IP를 제한한다면 패키지를 설치해도 수집이 실패할 수 있습니다. Actions 로그에서 `empty intraday data`·rate limit 등을 확인해 주세요.

## 4. 자동 실행 시각

`.github/workflows/market_monitor.yml`의 일정은 다음과 같습니다.

```yaml
on:
  schedule:
    - cron: '40 9-15 * * 1-5'
      timezone: 'America/New_York'
```

- 미국 동부시간 정규 거래일 09:40, 10:40, 11:40, 12:40, 13:40, 14:40, 15:40 — 하루 7번.
- 미국 서머타임은 `timezone` 설정이 자동 처리합니다.
- 주말·미국 증시 공식 휴장일·조기폐장 이후에는 Python에서 `pandas_market_calendars`로 추가로 걸러냅니다.
- GitHub 예약은 정확한 시각에 실행이 보장되지 않고 지연·누락될 수 있습니다. 특히 개장 직후 Yahoo의 5분봉이 지연되면 현재 세션 데이터가 없어서 해당 배포를 건너뛸 수 있습니다.
- 실패 시 지난 JSON을 최신 것처럼 다시 발행하지 않습니다. 기존 Pages의 과거 JSON은 남아 있으므로 ChatGPT가 `generated_utc`를 반드시 검증해야 합니다.
- 공개 저장소는 60일 이상 저장소 활동이 없으면 예정 실행이 비활성화될 수 있습니다. GitHub Actions에서 활성 상태를 가끔 확인하세요.

## 5. 생성 JSON 이해하기

```json
{
  "schema_version": 1,
  "source": "Yahoo Finance via yfinance (unofficial; may be delayed)",
  "mode": "regular_session",
  "verified_regular_session": true,
  "generated_utc": "...",
  "market": {"is_open_at_collection": true},
  "symbols": {
    "QQQ": {
      "last_price": 0.0,
      "data_age_minutes": 0.0,
      "weekly_indicators": {
        "last_completed_week": {"ema_200": 0.0, "rsi_14": 0.0},
        "current_week_provisional": {"ema_200": 0.0, "rsi_14": 0.0}
      }
    }
  }
}
```

이는 필드 구조를 보여주기 위한 **숫자 없는 샘플**입니다. 실제 값은 원자료로 계산됩니다.

- `last_price`: Yahoo Finance가 제공한 마지막 5분봉의 종가로, 실시간 확정 체결가가 아닙니다.
- `last_5m_bar_start_utc`: 최신 5분봉 시작 시각. `generated_utc`와 다른 개념입니다.
- `weekly_indicators.last_completed_week`: 이미 마감된 지난주까지의 **확정 주봉** 지표.
- `weekly_indicators.current_week_provisional`: 위 확정 주봉에 최신 5분봉 가격을 하나 추가해 계산한 **잠정 주봉** 지표.
- `ema_20`, `ema_50`, `ema_100`, `ema_200`: 최근 가격을 더 크게 반영하는 지수 이동평균선. 최초 EMA는 해당 길이의 단순 평균으로 초기화합니다.
- `rsi_14`: Wilder 방식의 주봉 RSI. RSI 평활선은 포함하지 않습니다.
- `provisional_price_to_ema_gap_pct`: 잠정 EMA 대비 현 가격 이격률.
- `buy_watch.price_band_status`: QQQM·VOO가 과거에 정한 관심 구간에 들어왔는지 나타내는 **관찰 상태**. 매수 주문 신호가 아닙니다.

**확정 주봉과 잠정 주봉을 혼동하지 마세요.** 배당 조정, 데이터 공급처, EMA 초기화 방식에 따라 TradingView의 지표 값과 차이가 날 수 있습니다.

## 6. ChatGPT의 기존 예약 작업에 연결하기

1. URL을 브라우저에서 열어 `generated_utc`, `mode`, `symbols.QQQ.last_price`가 나오는지 확인하세요.
2. **JSON의 실제 공개 URL을 ChatGPT에 보내세요.** 다음 단계에서는 내가 해당 URL을 읽을 수 있는지 먼저 시험해야 합니다.
3. **조회가 성공한 경우에만** 기존 `QQQ·ETF 시간별 점검` 작업의 지시문에 URL과 아래 `chatgpt_task_prompt.txt` 내용을 병합하세요. 새 중복 작업은 생성할 필요가 없습니다.
4. 예정된 ChatGPT 보고 시각을 미국 동부시간 09:55·10:55·…·15:55로 바꾸면, 매시 :40에 수집하는 GitHub 작업이 배포할 시간을 약 15분 확보할 수 있습니다. 다만 GitHub 실행·Pages 배포 지연으로 15분 내 완료되지 않을 수도 있습니다.
5. 수동 시험에서는 페이지를 정상적으로 읽었는지, `generated_utc` 최신 여부를 확인하는지, 이미 끝난 주봉 값과 진행 중인 주봉 값이 섞이지 않는지 검증하세요.

**제약:** ChatGPT의 예약 작업이 항상 임의의 외부 JSON을 fetch할 수 있다는 보장은 없습니다. GitHub Pages URL을 사용자 브라우저가 열 수 있어도 ChatGPT 예약 실행 환경에서는 접근이 막힐 수 있습니다. 이 경우 GitHub 커넥터 등 지원되는 앱 연결 경로를 별도로 검증하거나, 외부 자동 알림 서비스로 전환해야 합니다. `yfinance` 프로그램과 ChatGPT 예약 작업이 직접 API 수준에서 연결된 것은 아닙니다.

## 7. 종목 추가 방법

`collector.py` 파일의 다음 부분을 편집합니다.

```python
WATCHLIST = ("QQQ", "QQQM", "VOO", "HOOD", "BE", "ORCL")
```

미국 종목은 Yahoo Finance 티커를 확인하세요. 국내 종목을 추가하려면 예를 들어 `005930.KS`를 사용할 수 있지만, 미국 정규장에만 돌도록 짠 실행 주기·거래소 달력·통화·세션 검사는 한국주식에 맞게 **별도 변경**해야 합니다.

## 8. 장애·사용량·법적 제한

- `No publication: US equity market is closed`: 정상적으로 휴장 감지한 것입니다.
- `Data invalid: ... stale`: 지연된 시세를 현재 데이터로 잘못 내보내지 않기 위한 차단입니다.
- `only ... completed weeks`: yfinance가 장기 데이터 수집에 실패했거나 신규 상장 상품이라 200주가 없습니다.
- Pages URL 404: `Settings → Pages → Source: GitHub Actions` 및 `deploy` 작업 성공 여부를 확인하세요. 최초 게시까지 시간이 걸릴 수 있습니다.
- GitHub Actions 퍼블릭 저장소의 제공 조건과 실행 시간 한도는 GitHub 정책에 따릅니다.
- Yahoo Finance 데이터는 재배포·사용조건이 있을 수 있습니다. yfinance는 Yahoo의 공식 유료 API가 아니므로, 개인 연구 용도를 벗어나거나 대외 공개 서비스를 구축한다면 데이터 이용 약관을 반드시 검토하세요.
- ChatGPT 예약 작업은 별도의 사용자 사용 한도가 적용될 수 있으며, GitHub Actions와는 별도로 동작합니다.
