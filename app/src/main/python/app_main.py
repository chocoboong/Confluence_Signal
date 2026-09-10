# -*- coding: utf-8 -*-
"""
안드로이드 앱에서 부르는 진입점.

  코틀린 쪽에서 run(...) 하나만 호출한다.
  파이썬 쪽 파일(watch_download.py / watch_scan.py)은 PC 판과 거의 같다.
  달라진 것은 작업 폴더를 WATCH_HOME 환경변수로 받는다는 점뿐이다.

  설정 파일(관찰티커.txt / 텔레그램설정.txt)은 여기서 만들어 준다.
  앱 화면에서 고친 값이 그대로 파일이 되고, 아래 파이썬 코드는 PC 에서와
  똑같이 그 파일을 읽는다. 그래서 판정 로직에는 손댈 것이 없다.
"""
import os, sys, io, json, traceback, datetime

HOME = None


def _write_conf(home, tickers_text, index_text, token, chat_id, quiet, maxn):
    with io.open(os.path.join(home, '관찰티커.txt'), 'w', encoding='utf-8') as f:
        f.write('# 앱에서 자동 생성됩니다. 직접 고치지 마세요.\n')
        f.write('[관찰]\n')
        f.write((tickers_text or '').strip() + '\n\n')
        f.write('[지수]\n')
        f.write(((index_text or '').strip() or 'RSP') + '\n')

    with io.open(os.path.join(home, '텔레그램설정.txt'), 'w', encoding='utf-8') as f:
        f.write('봇토큰 = %s\n' % (token or '').strip())
        f.write('채팅ID = %s\n' % (chat_id or '').strip())
        f.write('조용한날도알림 = %s\n' % ('예' if quiet else '아니오'))
        f.write('한번에최대 = %s\n' % int(maxn or 20))


def _setup(home):
    global HOME
    HOME = home
    os.environ['WATCH_HOME'] = home
    os.makedirs(os.path.join(home, '데이터'), exist_ok=True)
    if home not in sys.path:
        sys.path.insert(0, home)


class _Tee(object):
    """화면 출력과 문자열 수집을 동시에."""
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)
        return len(s)

    def flush(self):
        pass

    def text(self):
        return ''.join(self.buf)


def _append_runlog(home, text):
    try:
        with io.open(os.path.join(home, 'run_log.txt'), 'a', encoding='utf-8') as f:
            f.write('\n======== %s ========\n'
                    % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            f.write(text)
    except Exception:
        pass


def _trim_runlog(home, keep=4000):
    """로그가 무한정 커지지 않게 뒤쪽만 남긴다."""
    p = os.path.join(home, 'run_log.txt')
    try:
        if not os.path.exists(p) or os.path.getsize(p) < 400000:
            return
        with io.open(p, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        with io.open(p, 'w', encoding='utf-8') as f:
            f.writelines(lines[-keep:])
    except Exception:
        pass


def run(home, tickers_text, index_text, token, chat_id, quiet, maxn, mode):
    """mode: 'scan' 전체 실행 / 'dry' 전송 없이 판정만 / 'test' 텔레그램 시험만

    돌려주는 값은 JSON 문자열 (코틀린이 파싱하기 쉽도록).
      {ok, mode, log, summary, last_date, regime, sent}
    """
    _setup(home)
    _write_conf(home, tickers_text, index_text, token, chat_id, quiet, maxn)

    tee = _Tee()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = tee
    ok, summary = False, ''
    try:
        import watch_scan
        if mode == 'test':
            conf = watch_scan.load_conf()
            good = watch_scan.send(
                conf, '테스트 메시지입니다. 타블렛 알림 설정이 정상입니다.\n보낸 시각 %s'
                % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
            print('테스트 전송 ' + ('성공' if good else '실패'))
            ok, summary = good, ('테스트 전송 성공' if good else '테스트 전송 실패')
        else:
            import watch_download
            watch_download.main()
            argv = sys.argv
            sys.argv = ['watch_scan'] + (['--dry'] if mode == 'dry' else [])
            try:
                rc = watch_scan.main()
            finally:
                sys.argv = argv
            ok = (rc == 0)
            summary = '완료' if ok else '오류'
    except Exception as e:
        print('\n[오류] %s: %s' % (type(e).__name__, e))
        traceback.print_exc()
        ok, summary = False, '%s: %s' % (type(e).__name__, e)
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    log = tee.text()
    _append_runlog(home, log)
    _trim_runlog(home)

    info = {'ok': ok, 'mode': mode, 'summary': summary, 'log': log[-20000:]}
    info.update(_status(home))
    return json.dumps(info, ensure_ascii=False)


def _status(home):
    """마지막 거래일·국면·최근 신호 수를 읽어 온다. 실패해도 조용히."""
    out = {'last_date': '', 'regime': '', 'signals': 0, 'watch_count': 0}
    try:
        import pandas as pd
        p = os.path.join(home, '데이터', '관찰_일봉.csv')
        if os.path.exists(p):
            d = pd.read_csv(p, usecols=['Ticker', 'Date'], dtype=str)
            out['last_date'] = str(d['Date'].max())
            out['watch_count'] = int(d['Ticker'].nunique())
        lg = os.path.join(home, '알림로그.csv')
        if os.path.exists(lg):
            g = pd.read_csv(lg, encoding='utf-8-sig')
            out['signals'] = int(len(g))
        rp = os.path.join(home, '관찰리포트.md')
        if os.path.exists(rp):
            with io.open(rp, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if line.startswith('| ') and ('켬' in line or '끔' in line):
                        out['regime'] = line.strip()
                        break
    except Exception:
        pass
    return out


def read_text(home, name, limit=200000):
    """앱 화면에서 로그·리포트를 보여줄 때 쓴다."""
    p = os.path.join(home, name)
    if not os.path.exists(p):
        return ''
    try:
        with io.open(p, encoding='utf-8', errors='replace') as f:
            s = f.read()
        return s[-limit:]
    except Exception as e:
        return '읽지 못했습니다: %s' % e
