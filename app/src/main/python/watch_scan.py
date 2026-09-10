# -*- coding: utf-8 -*-
"""
관찰 티커 Confluence Signal V3 스캔 + 텔레그램 알림

  사용법 : 같은 폴더의 `실행하기_관찰알림.bat` 을 더블클릭
  수동   : python watch_scan.py
           python watch_scan.py --test        텔레그램 연결만 시험 (신호 무관)
           python watch_scan.py --dry         판정만 하고 전송은 안 함
           python watch_scan.py --resend 5    최근 5거래일 신호를 중복 상관없이 다시 전송

판정 규칙 (Confluence Signal V3, 검증 원안 그대로)
  이벤트1  종가 < 볼린저 하단(20일선 - 2표준편차)
  이벤트2  볼린저 하단 상향 재진입
  이벤트3  RSI(5) 가 20 을 상향돌파
  매수 = 3봉 안에 2개 이상 합류 / 강한매수 = 3개 합류
  시장 국면 = 지수의 4요소(낙폭5%+ / 고변동 / 20일하락 / 200선아래) 중 2개 이상이면 '켬'
  국면은 거르지 않고 메시지에 표시만 한다 (사용자 선택: 국면 무관 전부 수신).

출력 : 알림로그.csv    보낸 신호와 그 뒤 성과
       관찰리포트.md   사람이 읽는 현황
       _알림기준일.txt 최초 실행일 (이전 신호를 몰아서 보내지 않게 하는 장치)
"""
import os, re, sys, json, datetime, urllib.request, urllib.error
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.environ.get('WATCH_HOME') or os.path.dirname(os.path.abspath(__file__))
DATADIR = os.path.join(HERE, '데이터')
WATCH_CSV = os.path.join(DATADIR, '관찰_일봉.csv')
INDEX_CSV = os.path.join(DATADIR, '지수_일봉.csv')
CONF_FILE = os.path.join(HERE, '텔레그램설정.txt')
LOG = os.path.join(HERE, '알림로그.csv')
REPORT = os.path.join(HERE, '관찰리포트.md')
BASEFILE = os.path.join(HERE, '_알림기준일.txt')
UNIVERSE_DIR = os.path.join(HERE, '..', '주가데이터')

STALE_DAYS = 5
SEND_WINDOW = 10   # 최근 몇 거래일 안의 신호까지 보낼지. 이보다 오래된 신호는
                   # 기록만 하고 보내지 않는다 (관찰티커.txt 에 종목을 새로 넣었을 때
                   # 그 종목의 옛 신호가 한꺼번에 쏟아지는 것을 막는 장치).
LOGCOLS = ['신호일', '티커', '종류', '합류', '스트레스', '국면', '요소', '신호일종가',
           '발송', '전송시각', '진입일', '진입가', '20봉만기', '20봉수익',
           '40봉만기', '40봉수익', '상태']


def load_conf():
    conf = {'봇토큰': '', '채팅ID': '', '조용한날도알림': '아니오', '한번에최대': '20'}
    if os.path.exists(CONF_FILE):
        try:
            with open(CONF_FILE, encoding='utf-8-sig') as f:
                for raw in f:
                    line = raw.split('#', 1)[0].strip()
                    if '=' not in line:
                        continue
                    k, _, v = line.partition('=')
                    conf[k.strip()] = v.strip()
        except Exception as e:
            print('[경고] 텔레그램설정.txt 를 읽지 못했습니다: %s' % e)
    try:
        conf['한번에최대'] = max(1, int(re.sub(r'\D', '', str(conf.get('한번에최대', '20'))) or 20))
    except Exception:
        conf['한번에최대'] = 20
    conf['조용한날도알림'] = str(conf.get('조용한날도알림', '')).strip() in ('예', 'Y', 'y', 'yes', '1')
    return conf


def send(conf, text):
    token, chat = conf.get('봇토큰', '').strip(), conf.get('채팅ID', '').strip()
    if not token or not chat:
        print('[안내] 텔레그램설정.txt 의 봇토큰/채팅ID 가 비어 있어 전송을 건너뜁니다.')
        return False
    url = 'https://api.telegram.org/bot%s/sendMessage' % token
    body = json.dumps({'chat_id': chat, 'text': text,
                       'disable_web_page_preview': True}).encode('utf-8')
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode('utf-8'))
            if res.get('ok'):
                return True
            print('[텔레그램 오류] %s' % res.get('description'))
            return False
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                pass
            print('[텔레그램 실패 %d/3] HTTP %s %s' % (attempt + 1, e.code, detail))
            if e.code in (400, 401, 403, 404):
                return False
        except Exception as e:
            print('[텔레그램 실패 %d/3] %s' % (attempt + 1, e))
    return False


def load_prices(path, label):
    if not os.path.exists(path):
        raise FileNotFoundError('%s 데이터가 없습니다: %s -- 먼저 watch_download.py 를 실행하세요.'
                                % (label, path))
    d = pd.read_csv(path, parse_dates=['Date']).rename(columns={'Adj Close': 'AdjClose'})
    fac = d['AdjClose'] / d['Close']
    for c in ['Open', 'High', 'Low', 'Close']:
        d[c] = d[c] * fac
    d = d[d['Volume'] > 0].sort_values(['Ticker', 'Date']).reset_index(drop=True)
    return d.drop(columns=['AdjClose'])


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_signals(df):
    """Confluence V2/V3 매수 이벤트. 주간스캔.py 와 같은 계산이다."""
    G = df.groupby('Ticker', sort=False)
    ma20 = G['Close'].transform(lambda s: s.rolling(20).mean())
    sd20 = G['Close'].transform(lambda s: s.rolling(20).std())
    lower = ma20 - 2 * sd20
    r5 = G['Close'].transform(lambda s: rsi(s, 5))
    prev = lambda s: s.groupby(df['Ticker'], sort=False).shift(1)
    e1 = df['Close'] < lower
    e2 = (df['Close'] > lower) & (prev(df['Close']) <= prev(lower))
    e3 = (r5 > 20) & (prev(r5) <= 20)

    def recent(x, w=3):
        return x.astype(float).groupby(df['Ticker'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).max()).fillna(0).astype(int)

    conf = recent(e1) + recent(e2) + recent(e3)
    pconf = conf.groupby(df['Ticker'], sort=False).shift(1).fillna(0)
    df['합류'] = conf
    df['매수'] = (conf >= 2) & (pconf < 2)
    df['강한매수'] = (conf >= 3) & (pconf < 3)
    return df


def regime_from_series(s):
    """조정종가 시계열 하나로 4요소 스트레스를 계산한다."""
    u = s.pct_change()
    dd = s / s.rolling(252, min_periods=60).max() - 1
    v = u.rolling(20).std()
    vm = v.rolling(252, min_periods=120).median()
    m20 = s / s.shift(20) - 1
    s200 = s.rolling(200).mean()
    fl = pd.DataFrame({'낙폭5': dd <= -0.05, '고변동': v > vm,
                       '20일하락': m20 <= 0, '200선아래': s <= s200})
    ok = m20.notna() & vm.notna() & s200.notna()
    st = fl.sum(axis=1).where(ok)
    r = pd.DataFrame({'스트레스': st, '국면': np.where(st >= 2, '켬', '끔'), '지수낙폭': dd,
                      '요소': fl.apply(lambda x: ''.join('O' if x[c] else '-' for c in fl.columns),
                                       axis=1)})
    r.loc[st.isna(), '국면'] = '미정'
    return r


def regime_from_universe(limit_date):
    """참고용. 주간스캔이 쓰는 222종목 동일가중 지수로 같은 판정을 해 본다.
    데이터가 없거나 2주 넘게 오래됐으면 조용히 None."""
    import glob
    try:
        frames = []
        for fn in ['megacap', 'midcap', 'smallcap']:
            cand = glob.glob(os.path.join(UNIVERSE_DIR, '%s_combined_*.csv' % fn))
            if not cand:
                return None
            f = max(cand, key=lambda x: pd.read_csv(x, usecols=['Date'])['Date'].max())
            frames.append(pd.read_csv(f, parse_dates=['Date'],
                                      usecols=['Ticker', 'Date', 'Close', 'Adj Close', 'Volume']))
        d = pd.concat(frames, ignore_index=True).rename(columns={'Adj Close': 'AdjClose'})
        d['Close'] = d['AdjClose']
        d = d[d['Volume'] > 0]
        if (pd.Timestamp.today().normalize() - d['Date'].max()).days > 14:
            return None
        dates = np.sort(d['Date'].unique())
        u = d.groupby('Ticker', sort=False)['Close'].pct_change().groupby(d['Date']).mean().reindex(dates)
        idx = pd.Series((1 + u.fillna(0)).cumprod().values, index=pd.DatetimeIndex(dates))
        return regime_from_series(idx)
    except Exception:
        return None


OBJCOLS = ['신호일', '티커', '종류', '국면', '요소', '발송', '전송시각',
           '진입일', '20봉만기', '40봉만기', '상태']


def as_object(log):
    """전부 NaN 인 컬럼은 float64 가 되어 문자열 대입 때 경고/오류가 난다. 미리 object 로 굳힌다."""
    for c in OBJCOLS:
        if c in log.columns:
            log[c] = log[c].astype(object)
    return log


def read_log():
    if not os.path.exists(LOG):
        return pd.DataFrame(columns=LOGCOLS)
    log = pd.read_csv(LOG, encoding='utf-8-sig')
    for c in LOGCOLS:
        if c not in log.columns:
            log[c] = np.nan
    log = as_object(log)
    for c in OBJCOLS:
        log[c] = log[c].map(lambda x: str(x) if pd.notna(x) else np.nan)
    return log[LOGCOLS]


def fill_results(log, df):
    """신호 다음날 종가로 진입했다고 보고 20봉/40봉 뒤 성과를 채운다."""
    if log.empty:
        return log
    log = as_object(log)
    px = df.pivot_table(index='Date', columns='Ticker', values='Close')
    dts = list(px.index)
    for i, r in log.iterrows():
        t, d = r['티커'], pd.Timestamp(r['신호일'])
        if t not in px.columns or d not in px.index:
            continue
        p = dts.index(d)
        if p + 1 >= len(dts):
            continue
        entry = px[t].iloc[p + 1]
        if pd.isna(entry):
            continue
        log.at[i, '진입일'] = str(dts[p + 1].date())
        log.at[i, '진입가'] = round(float(entry), 4)
        for h, cd, cr in [(20, '20봉만기', '20봉수익'), (40, '40봉만기', '40봉수익')]:
            if p + 1 + h < len(dts):
                ex = px[t].iloc[p + 1 + h]
                if pd.notna(ex):
                    log.at[i, cd] = str(dts[p + 1 + h].date())
                    log.at[i, cr] = round(float(ex) / float(entry) - 1, 4)
    log['상태'] = np.where(log['40봉수익'].notna(), '완료',
                  np.where(log['20봉수익'].notna(), '20봉완료', '진행중'))
    return log


def build_message(rows, g, last, age, quiet, notes, maxn):
    stress = int(g['스트레스']) if pd.notna(g['스트레스']) else -1
    head = []
    if age > STALE_DAYS:
        head.append('[주의] 데이터가 %d일 지났습니다. 다운로드가 실패했을 수 있습니다.' % age)
    head.append('관찰 신호 %s  (%s 종가 기준)'
                % ('%d건' % len(rows) if len(rows) else '없음', last.date()))
    head.append('시장 국면 %s %d/4  (지수 낙폭 %+.1f%%)'
                % (g['국면'], stress, float(g['지수낙폭']) * 100))
    body = []
    if len(rows):
        shown = rows.head(maxn)
        for _, r in shown.iterrows():
            body.append('- %s  %s %s/3  종가 %.2f'
                        % (r['티커'], r['종류'], r['합류'], float(r['신호일종가'])))
        if len(rows) > maxn:
            body.append('... 외 %d건 (전체는 관찰리포트.md 참고)' % (len(rows) - maxn))
    elif quiet:
        body.append('오늘 나온 신호는 없습니다. 연 2.9회짜리 신호라 몇 주씩 비는 게 정상입니다.')
    tail = []
    if len(rows):
        if g['국면'] == '켬':
            tail.append('국면 켬 구간 검증값: 20봉 승률 58.3%, 평균 +3.74%')
        elif g['국면'] == '끔':
            tail.append('[국면 끔] 이 구간의 매수 신호는 검증에서 우위가 0이었습니다 (-0.1%p, 귀무 39%).')
        else:
            tail.append('국면 판정에 필요한 지수 이력이 아직 모자랍니다.')
    tail += [n for n in notes if n]
    return '\n'.join(head + [''] + body + ([''] + tail if tail else []))


def write_report(log, watch, g, idx_sym, last, age, basedate):
    A = []
    a = A.append
    a('# 관찰 티커 알림 현황 - %s\n' % datetime.date.today())
    if age > STALE_DAYS:
        a('> [주의] 데이터가 %d일 지났습니다. `실행하기_관찰알림.bat` 을 돌려 보세요.\n' % age)
    a('관찰 종목 **%d개** / 데이터 마지막 거래일 **%s** / 알림 기준일 %s\n'
      % (watch['Ticker'].nunique(), last.date(), basedate.date()))
    stress = int(g['스트레스']) if pd.notna(g['스트레스']) else -1
    a('## 시장 국면\n')
    a('| 지수 | 스트레스 | 국면 | 지수 낙폭 | 낙폭5%+/고변동/20일하락/200선아래 |')
    a('|---|---|---|---|---|')
    a('| %s | **%d/4** | **%s** | %+.2f%% | %s |'
      % (idx_sym, stress, g['국면'], float(g['지수낙폭']) * 100, g['요소']))
    ureg = regime_from_universe(last)
    if ureg is not None:
        ud = ureg.index[ureg.index <= last]
        if len(ud):
            ug = ureg.loc[ud.max()]
            uv = int(ug['스트레스']) if pd.notna(ug['스트레스']) else -1
            a('| 222종목 동일가중 (검증 원안, 주간스캔 데이터) | %d/4 | %s | %+.2f%% | %s |'
              % (uv, ug['국면'], float(ug['지수낙폭']) * 100, ug['요소']))
            if ug['국면'] != g['국면']:
                a('')
                a('> [주의] 두 지수의 판정이 갈렸습니다. 검증 원안은 **222종목 동일가중** 쪽입니다.')
                a('> 알림 메시지는 %s 기준으로 나갑니다.' % idx_sym)
    a('')
    if g['국면'] == '끔':
        a('국면이 **끔**입니다. 이 구간의 매수 신호는 검증에서 우위가 0이었습니다 '
          '(-0.1%p, 귀무 39%). 신호는 보내되 크기를 줄이거나 거르는 판단은 직접 하세요.\n')
    recent = log[pd.to_datetime(log['신호일']) >= last - pd.Timedelta(days=30)] if len(log) else log
    a('## 최근 30일 신호 %d건\n' % len(recent))
    if len(recent):
        a('| 신호일 | 티커 | 종류 | 합류 | 국면 | 종가 | 발송 | 20봉수익 |')
        a('|---|---|---|---|---|---|---|---|')
        for _, r in recent.sort_values('신호일', ascending=False).iterrows():
            ret = '' if pd.isna(r['20봉수익']) else '%+.1f%%' % (float(r['20봉수익']) * 100)
            a('| %s | **%s** | %s | %s/3 | %s | %.2f | %s | %s |'
              % (r['신호일'], r['티커'], r['종류'], r['합류'], r['국면'],
                 float(r['신호일종가']), r['발송'], ret))
    else:
        a('없습니다. 연 2.9회짜리 신호라 몇 주씩 비는 게 정상입니다.')
    sent = log[log['발송'] == '전송'] if len(log) else log
    h20 = sent[sent['20봉수익'].notna()] if len(sent) else sent
    a('\n## 알림으로 받은 신호의 성적\n')
    a('- 전송 **%d건** (초기기록 %d건은 제외)'
      % (len(sent), int(log['발송'].isin(['초기기록', '지난신호']).sum()) if len(log) else 0))
    if len(h20) >= 5:
        for lab, sub in [('전체', h20), ('국면 켬', h20[h20['국면'] == '켬']),
                         ('국면 끔', h20[h20['국면'] == '끔'])]:
            if len(sub) >= 5:
                a('- %s 20봉: %d건, 승률 **%.0f%%**, 평균 **%+.2f%%**'
                  % (lab, len(sub), (sub['20봉수익'] > 0).mean() * 100,
                     sub['20봉수익'].mean() * 100))
    else:
        a('- 아직 20봉이 지난 기록이 5건 미만입니다. 신호 발생 + 20거래일이 지나야 채워집니다.')
    a('\n> 기준선: 국면 켬에서 20봉 승률 58.3%, 평균 +3.74% (과거 검증값).')
    a('> 관찰 종목은 소수라 표본이 훨씬 느리게 쌓입니다. 통계로 읽지 말고 기록으로만 보세요.')
    a('\n전체 기록: `알림로그.csv`')
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(A))


def main():
    argv = sys.argv[1:]
    dry = '--dry' in argv
    resend = 0
    if '--resend' in argv:
        try:
            resend = int(argv[argv.index('--resend') + 1])
        except Exception:
            resend = 5
    conf = load_conf()

    if '--test' in argv:
        ok = send(conf, '테스트 메시지입니다. 관찰 알림 설정이 정상입니다.\n보낸 시각 %s'
                  % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
        print('테스트 전송 ' + ('성공' if ok else '실패'))
        return 0 if ok else 1

    watch = build_signals(load_prices(WATCH_CSV, '관찰'))
    idx = load_prices(INDEX_CSV, '지수')
    idx_sym = str(idx['Ticker'].iloc[0])
    reg = regime_from_series(idx.sort_values('Date').set_index('Date')['Close'])

    last = pd.Timestamp(watch['Date'].max())
    age = (pd.Timestamp.today().normalize() - last).days
    rdates = reg.index[reg.index <= last]
    g = reg.loc[rdates.max()] if len(rdates) else pd.Series(
        {'스트레스': np.nan, '국면': '미정', '지수낙폭': 0.0, '요소': '----'})

    first_run = not os.path.exists(BASEFILE)
    if first_run:
        with open(BASEFILE, 'w', encoding='utf-8') as f:
            f.write(str(last.date()))
    basedate = pd.Timestamp(open(BASEFILE, encoding='utf-8').read().strip())

    hits = watch[watch['매수'] | watch['강한매수']].copy()
    hits['종류'] = np.where(hits['강한매수'], '강한매수', '매수')
    rows = []
    for _, r in hits.iterrows():
        rd = reg.index[reg.index <= r['Date']]
        gg = reg.loc[rd.max()] if len(rd) else None
        rows.append(dict(신호일=str(r['Date'].date()), 티커=str(r['Ticker']), 종류=r['종류'],
                         합류=int(r['합류']),
                         스트레스=(int(gg['스트레스']) if gg is not None and pd.notna(gg['스트레스']) else -1),
                         국면=(gg['국면'] if gg is not None else '미정'),
                         요소=(gg['요소'] if gg is not None else '----'),
                         신호일종가=round(float(r['Close']), 4)))
    allsig = pd.DataFrame(rows, columns=['신호일', '티커', '종류', '합류', '스트레스',
                                         '국면', '요소', '신호일종가'])
    if not allsig.empty:
        allsig = allsig.drop_duplicates(subset=['신호일', '티커'], keep='last')

    log = read_log()
    known = set(zip(log['신호일'].astype(str), log['티커'].astype(str))) if len(log) else set()
    if not allsig.empty:
        keep = [(a, b) not in known for a, b in
                zip(allsig['신호일'].astype(str), allsig['티커'].astype(str))]
        fresh = allsig[keep].copy()
    else:
        fresh = allsig.copy()
    dates_all = np.sort(watch['Date'].unique())
    window_cut = pd.Timestamp(dates_all[max(0, len(dates_all) - SEND_WINDOW)])
    if not fresh.empty:
        # 기준일 이후 + 최근 SEND_WINDOW 거래일 안의 신호만 보낸다.
        # 최초 실행분은 '초기기록', 창 밖의 옛 신호(종목을 새로 넣은 경우)는 '지난신호'.
        sd = pd.to_datetime(fresh['신호일'])
        fresh['발송'] = np.where(sd <= basedate, '초기기록',
                         np.where(sd >= window_cut, '대기', '지난신호'))
        fresh['전송시각'] = np.nan
        for c in LOGCOLS:
            if c not in fresh.columns:
                fresh[c] = np.nan
        log = (fresh[LOGCOLS].copy() if log.empty
               else pd.concat([log, fresh[LOGCOLS]], ignore_index=True))
    if len(log):
        log = as_object(log.drop_duplicates(subset=['신호일', '티커'],
                                            keep='first').reset_index(drop=True))

    send_mask = (log['발송'] == '대기') if len(log) else pd.Series(dtype=bool)
    if resend and len(log):
        cut = str(pd.Timestamp(dates_all[max(0, len(dates_all) - resend)]).date())
        send_mask = send_mask | (log['신호일'].astype(str) >= cut)
    to_send = log[send_mask].sort_values(['신호일', '티커']) if len(log) else log

    notes = []
    if first_run:
        notes.append('처음 실행이라 %s 까지의 과거 신호 %d건은 기록만 하고 보내지 않았습니다. '
                     '다음 실행부터 새로 나온 신호만 옵니다.'
                     % (basedate.date(), int((log['발송'] == '초기기록').sum()) if len(log) else 0))

    quiet = conf['조용한날도알림']
    msg = build_message(to_send, g, last, age, quiet, notes, conf['한번에최대'])
    if len(to_send) or quiet or first_run:
        if dry:
            print('--- (dry) 보냈을 메시지 ---')
            print(msg)
            print('---------------------------')
        elif send(conf, msg):
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            mark = send_mask & (log['발송'] == '대기')
            log.loc[mark, '전송시각'] = now
            log.loc[mark, '발송'] = '전송'
            print('텔레그램 전송 완료: %d건' % len(to_send))
        elif len(to_send):
            print('[주의] 전송 실패. 발송 상태를 "대기"로 남겨 다음 실행 때 다시 시도합니다.')
    else:
        print('보낼 신호가 없습니다.')

    if len(log):
        log = log[LOGCOLS].sort_values(['신호일', '티커']).reset_index(drop=True)
        log = fill_results(log, watch)
    log.to_csv(LOG, index=False, encoding='utf-8-sig')
    write_report(log, watch, g, idx_sym, last, age, basedate)

    print('로그 %d건 -> %s' % (len(log), LOG))
    print('마지막 거래일 %s (%d일 전), 지수 %s, 국면 %s, 이번에 보낼 신호 %d건'
          % (last.date(), age, idx_sym, g['국면'], len(to_send)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except FileNotFoundError as e:
        print('')
        print('[중단] %s' % e)
        print('')
        print('확인할 것:')
        print('  1) 관찰티커.txt 의 [관찰] 아래에 종목을 적었는지')
        print('  2) 실행하기_관찰알림.bat 을 돌려 다운로드가 성공했는지 (run_log.txt 확인)')
        sys.exit(1)
    except Exception as e:
        print('')
        print('[오류] %s: %s' % (type(e).__name__, e))
        print('이 메시지를 그대로 복사해 두면 원인을 찾기 쉽습니다.')
        sys.exit(1)
