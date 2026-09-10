package com.jiye.watchalert

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject

/** 파이썬(app_main.py)을 부르는 유일한 통로. */
object PyRunner {

    private fun start(c: Context) {
        if (!Python.isStarted()) Python.start(AndroidPlatform(c.applicationContext))
    }

    /** mode: "scan" 전체 / "dry" 전송 없이 / "test" 텔레그램 시험만 */
    fun run(c: Context, mode: String): JSONObject {
        start(c)
        val py = Python.getInstance()
        val mod = py.getModule("app_main")
        val out = mod.callAttr(
            "run",
            Prefs.home(c),
            Prefs.getStr(c, Prefs.K_TICKERS),
            Prefs.getStr(c, Prefs.K_INDEX, "RSP"),
            Prefs.getStr(c, Prefs.K_TOKEN),
            Prefs.getStr(c, Prefs.K_CHAT),
            Prefs.getBool(c, Prefs.K_QUIET, false),
            Prefs.getInt(c, Prefs.K_MAXN, 20),
            mode
        ).toString()
        return try {
            JSONObject(out)
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("summary", "결과를 읽지 못했습니다").put("log", out)
        }
    }

    fun readText(c: Context, name: String): String {
        start(c)
        return try {
            Python.getInstance().getModule("app_main")
                .callAttr("read_text", Prefs.home(c), name).toString()
        } catch (e: Exception) {
            "읽지 못했습니다: ${e.message}"
        }
    }
}
