package com.jiye.watchalert

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var busy = false

    private lateinit var tvStatus: TextView
    private lateinit var tvLog: TextView
    private lateinit var etTickers: EditText
    private lateinit var etIndex: EditText
    private lateinit var etToken: EditText
    private lateinit var etChat: EditText
    private lateinit var etMaxn: EditText
    private lateinit var cbQuiet: CheckBox
    private lateinit var swAuto: CheckBox
    private lateinit var btnRun: Button
    private lateinit var btnTest: Button

    private val askNotify =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        ScanWorker.ensureChannels(this)

        tvStatus = findViewById(R.id.tvStatus)
        tvLog = findViewById(R.id.tvLog)
        etTickers = findViewById(R.id.etTickers)
        etIndex = findViewById(R.id.etIndex)
        etToken = findViewById(R.id.etToken)
        etChat = findViewById(R.id.etChat)
        etMaxn = findViewById(R.id.etMaxn)
        cbQuiet = findViewById(R.id.cbQuiet)
        swAuto = findViewById(R.id.swAuto)
        btnRun = findViewById(R.id.btnRun)
        btnTest = findViewById(R.id.btnTest)

        load()
        askNotificationPermission()

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            save()
            Toast.makeText(this, "저장했습니다", Toast.LENGTH_SHORT).show()
            showStatus()
        }
        btnRun.setOnClickListener { save(); runPython("scan") }
        btnTest.setOnClickListener { save(); runPython("test") }
        findViewById<Button>(R.id.btnReport).setOnClickListener { showFile("관찰리포트.md") }
        findViewById<Button>(R.id.btnLog).setOnClickListener { showFile("run_log.txt") }

        swAuto.setOnCheckedChangeListener { _, on ->
            Prefs.put(this, Prefs.K_AUTO, on)
            if (on) Scheduler.enable(this) else Scheduler.disable(this)
            showStatus()
        }

        showStatus()
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun load() {
        etTickers.setText(Prefs.getStr(this, Prefs.K_TICKERS))
        etIndex.setText(Prefs.getStr(this, Prefs.K_INDEX, "RSP"))
        etToken.setText(Prefs.getStr(this, Prefs.K_TOKEN))
        etChat.setText(Prefs.getStr(this, Prefs.K_CHAT))
        etMaxn.setText(Prefs.getInt(this, Prefs.K_MAXN, 20).toString())
        cbQuiet.isChecked = Prefs.getBool(this, Prefs.K_QUIET, false)
        swAuto.isChecked = Prefs.getBool(this, Prefs.K_AUTO, false)
    }

    private fun save() {
        Prefs.put(this, Prefs.K_TICKERS, etTickers.text.toString())
        Prefs.put(this, Prefs.K_INDEX, etIndex.text.toString().ifBlank { "RSP" })
        Prefs.put(this, Prefs.K_TOKEN, etToken.text.toString().trim())
        Prefs.put(this, Prefs.K_CHAT, etChat.text.toString().trim())
        Prefs.put(this, Prefs.K_QUIET, cbQuiet.isChecked)
        Prefs.put(this, Prefs.K_MAXN, etMaxn.text.toString().trim().toIntOrNull() ?: 20)
    }

    private fun countTickers(): Int =
        etTickers.text.toString().split(',', ' ', '\n', '\t')
            .map { it.trim() }.count { it.isNotEmpty() }

    private fun showStatus() {
        val last = Prefs.getStr(this, Prefs.K_LAST_SUM, "아직 실행한 적 없음")
        val auto = if (Prefs.getBool(this, Prefs.K_AUTO, false)) "켜짐" else "꺼짐"
        val (_, why) = Scheduler.shouldRunNow(this)
        tvStatus.text = buildString {
            append("관찰 종목 ${countTickers()}개\n")
            append("자동 실행 $auto  ·  지금 판단: $why\n")
            append("마지막 실행: $last\n")
            append("저장 위치: ${Prefs.home(this@MainActivity)}")
        }
    }

    private fun setBusy(on: Boolean) {
        busy = on
        btnRun.isEnabled = !on
        btnTest.isEnabled = !on
        btnRun.text = if (on) "실행 중..." else "지금 실행"
    }

    private fun runPython(mode: String) {
        if (busy) return
        setBusy(true)
        tvLog.text = "실행 중입니다. 종목 수에 따라 2~5분 걸립니다.\n화면을 꺼도 계속됩니다."
        io.execute {
            val res = try {
                PyRunner.run(this, mode)
            } catch (e: Throwable) {
                org.json.JSONObject().put("ok", false)
                    .put("summary", "${e.javaClass.simpleName}: ${e.message}")
                    .put("log", android.util.Log.getStackTraceString(e))
            }
            ui.post {
                setBusy(false)
                val ok = res.optBoolean("ok", false)
                val summary = res.optString("summary", "")
                Prefs.put(this, Prefs.K_LAST_SUM, "${Scheduler.todayKst()} $summary")
                if (ok && mode == "scan") Prefs.put(this, Prefs.K_LAST_OK, Scheduler.todayKst())
                tvLog.text = res.optString("log", "")
                showStatus()
                Toast.makeText(this, if (ok) "완료: $summary" else "실패: $summary",
                    Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showFile(name: String) {
        tvLog.text = "읽는 중..."
        io.execute {
            val s = PyRunner.readText(this, name)
            ui.post { tvLog.text = if (s.isBlank()) "($name 아직 없음)" else s }
        }
    }
}
