import html, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd, requests

# ================= PENGATURAN (boleh diubah) =================
MODAL = 5_000_000    # angka awal di kotak modal, rupiah (bisa diganti di halaman)
RISIKO = 1           # angka awal maksimal rugi per transaksi, % dari modal
MIN_TAMPIL = 50      # volume 24 jam di bawah ini (juta rupiah): koin dibuang dari halaman
MIN_LIKUID = 200     # di bawah ini (juta rupiah): tampil tapi dicap kurang likuid
RAMAI = 1000         # di atas ini (juta rupiah): dicap ramai
YAKIN_PENUH = 1.0    # nilai belajar terbaik segini = suara strategi dipercaya penuh
JUMLAH_TOP = 5
HARI_DATA = 400      # berapa hari lilin 4 jam yang ditarik
MIN_LILIN = 500      # koin dengan lilin kurang dari ini dilewati
AREA_LILIN = 250     # area demand/supply dicari dari sekian lilin terakhir (sekitar 6 minggu)
AREA_JENDELA = 5
AREA_MIN_SENTUH = 2
AREA_BUFFER = 0.5
TARGET_MAKS_ATR = 6
RR_MIN = 1.5
STOP_ATR = 2
TARGET_ATR = 3
VOLUME_RAMAI = 1.5
BIAYA_BELI = 0.0034  # biaya beli di Indodax pasar rupiah (taker, sudah termasuk pajak dan biaya bursa)
BIAYA_JUAL = 0.0032  # biaya jual
TUNGGU_LILIN = 6     # rekam jejak: nunggu harga nyentuh area beli maksimal 6 lilin (24 jam)
MAKS_LILIN = 30      # rekam jejak: posisi ditutup paksa setelah 30 lilin (5 hari)
MIN_BERARTI = 30
GRAFIK_LILIN = 250
# =============================================================

BIAYA = (BIAYA_BELI + BIAYA_JUAL) / 2
LABEL = ["🟢 Layak beli", "🟡 Pantau / cicil", "⚪ Tunggu", "🔴 Hindari dulu", "⚫ Kurang likuid"]
TEKS_AREA = {"di": "✅ Di area beli", "dekat": "🟡 Dekat area beli", "jauh": "⏳ Jauh, tunggu turun",
             "": "Area demand gak ketemu"}
TEKS_UJI = {"lulus": "LULUS", "tipis": "LULUS TIPIS, kalah jauh dari beli-diamkan",
            "gagal": "GAGAL, sinyalnya kurang bisa dipercaya"}
BOBOT_UJI = {"lulus": 1.0, "tipis": 0.7, "gagal": 0.4}
NAMA_STATUS = {"target": "🎯 Kena target", "stop": "🛑 Kena stop loss", "waktu": "⏱ Waktu habis"}
SELESAI, TERBUKA = ("target", "stop", "waktu"), ("menunggu", "berjalan")
BASIS = "https://indodax.com"
KEPALA = {"User-Agent": "Mozilla/5.0"}
FMT = "%Y-%m-%d %H:%M"

# ---------- AMBIL DATA DARI INDODAX ----------
def ambil_lilin(kode):
    """Lilin 4 jam (waktu WIB). Kalau lilin 4 jam gak tersedia, ambil per jam lalu digabung."""
    sampai = int(time.time())
    dari = sampai - HARI_DATA * 86400
    for tf in ("240", "60"):
        try:
            r = requests.get(BASIS + "/tradingview/history_v2", headers=KEPALA, timeout=30,
                             params={"symbol": kode + "IDR", "tf": tf, "from": dari, "to": sampai})
            data = r.json()
            if not isinstance(data, list) or len(data) < 50:
                continue
            df = pd.DataFrame(data)
            df.columns = [str(c).lower() for c in df.columns]
            detik = df["time"].astype(float)
            if detik.iloc[-1] > 1e11:
                detik = detik / 1000
            df.index = pd.to_datetime(detik, unit="s") + pd.Timedelta(hours=7)
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            df.columns = ["Open", "High", "Low", "Close", "Volume"]
            df = df[~df.index.duplicated(keep="last")].sort_index()
            if tf == "60":
                df = df.resample("4h").agg({"Open": "first", "High": "max", "Low": "min",
                                            "Close": "last", "Volume": "sum"}).dropna()
            df = df[(df["Close"] > 0) & (df["Low"] > 0)].iloc[:-1]      # buang lilin yang masih berjalan
            if len(df) < MIN_LILIN:
                continue
            mati = float((df["High"] == df["Low"]).iloc[-100:].mean())   # lilin rata = gak ada transaksi
            if mati < 0.5:
                return df
        except Exception:
            pass
        time.sleep(1)
    return None

def ambil_volume():
    """Volume 24 jam dalam rupiah per koin. Kalau gagal, saringan likuiditas dilewati."""
    try:
        t = requests.get(BASIS + "/api/ticker_all", headers=KEPALA, timeout=30).json().get("tickers", {})
        return {k[:-4].upper(): float(v.get("vol_idr", 0)) for k, v in t.items() if k.endswith("_idr")}
    except Exception:
        return {}

def baca_daftar():
    kode = []
    with open("koin.txt") as fh:
        for baris in fh:
            baris = baris.split("#")[0]
            for k in baris.replace(";", ",").replace(" ", ",").split(","):
                k = k.strip().upper()
                if k.endswith("IDR") and len(k) > 3:
                    k = k[:-3]
                if k and k not in kode:
                    kode.append(k)
    return kode

# ---------- ANGKA DAN FORMAT ----------
def bulat(h):
    h = float(h)
    if h >= 1000:
        return float(round(h))
    if h >= 1:
        return round(h, 2)
    return float(np.format_float_positional(h, precision=4, unique=False, fractional=False, trim="-"))

def harga_teks(h):
    h = float(h)
    if h >= 1000:
        return f"{h:,.0f}".replace(",", ".")
    if h >= 1:
        return f"{h:.2f}".replace(".", ",")
    return np.format_float_positional(h, precision=4, unique=False, fractional=False, trim="-").replace(".", ",")

def rp(h):
    return "Rp " + harga_teks(h)

def vol_teks(v):
    if v is None:
        return "tidak diketahui"
    if v >= 1e9:
        return "Rp " + f"{v / 1e9:.1f}".replace(".", ",") + " miliar"
    return f"Rp {v / 1e6:.0f} juta"

def e(x):
    return html.escape(str(x))

def pct(x):
    return "-" if x is None else f"{x * 100:+.2f}%"

# ---------- INDIKATOR ----------
def rsi(c, n=14):
    d = c.diff()
    naik = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    turun = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + naik / turun.replace(0, np.nan))

def atr(df, n=14):
    c0 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c0).abs(), (df["Low"] - c0).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def info_macd(c):
    garis = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sinyal = garis.ewm(span=9, adjust=False).mean()
    return garis, sinyal, garis - sinyal

# ---------- STRATEGI DAN GABUNGANNYA ----------
def posisi_dari_sinyal(masuk, keluar):
    s = pd.Series(np.nan, index=masuk.index)
    s[masuk] = 1.0
    s[keluar] = 0.0
    return s.ffill().fillna(0.0)

def kandidat(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    out = []
    for f, s in [(5, 20), (10, 30), (10, 50), (20, 50), (20, 100), (50, 200)]:
        out.append((f"MA cross {f}/{s}", (c.rolling(f).mean() > c.rolling(s).mean()).astype(float)))
    r = rsi(c)
    for lo, hi in [(30, 70), (25, 65), (35, 65)]:
        out.append((f"RSI beli <{lo}, jual >{hi}", posisi_dari_sinyal(r < lo, r > hi)))
    ramai = v > VOLUME_RAMAI * v.rolling(20).mean()
    for n, m in [(20, 10), (55, 20)]:
        tembus = c > h.rolling(n).max().shift(1)
        jebol = c < l.rolling(m).min().shift(1)
        out.append((f"Breakout {n} lilin", posisi_dari_sinyal(tembus, jebol)))
        out.append((f"Breakout {n} lilin + volume", posisi_dari_sinyal(tembus & ramai, jebol)))
    return out

def hasil_per_lilin(c, pos):
    p = pos.shift(1).fillna(0)
    return p * c.pct_change().fillna(0) - p.diff().abs().fillna(0) * BIAYA

def sharpe(r):
    return 0.0 if r.std() == 0 else float(r.mean() / r.std() * np.sqrt(2190))

def total(r):
    return float((1 + r).prod() - 1)

def belajar(df):
    """Bobot dari data belajar. Kalau gak ada strategi yang meyakinkan, suaranya dikecilin."""
    c = df["Close"]
    batas = int(len(df) * 0.7)
    tabel, daftar_pos = [], []
    for nama, pos in kandidat(df):
        r = hasil_per_lilin(c, pos)
        tabel.append({"nama": nama, "sharpe_latih": sharpe(r.iloc[:batas]),
                      "ret_uji": total(r.iloc[batas:]), "posisi": int(pos.iloc[-1])})
        daftar_pos.append(pos)
    w = np.array([max(t["sharpe_latih"], 0.0) for t in tabel])
    terbaik = float(w.max())
    yakin = float(min(1.0, terbaik / YAKIN_PENUH))          # 0 kalau gak ada strategi yang untung
    w = w / w.sum() if w.sum() > 0 else np.zeros(len(tabel))
    suara = pd.Series(0.0, index=c.index)
    for t, bobot, pos in zip(tabel, w, daftar_pos):
        t["bobot"] = float(bobot)
        suara = suara + bobot * (2 * pos - 1)
    pos_gab = (suara > 0).astype(float)
    r = hasil_per_lilin(c, pos_gab)
    mentah = float(suara.iloc[-1])
    m = {"pos": pos_gab, "suara_mentah": mentah, "yakin": yakin, "terbaik": terbaik, "suara": mentah * yakin,
         "tabel": sorted(tabel, key=lambda t: t["bobot"], reverse=True),
         "jumlah_beli": sum(t["posisi"] for t in tabel), "jumlah": len(tabel),
         "ret_uji": total(r.iloc[batas:]), "sharpe_uji": sharpe(r.iloc[batas:]),
         "bh_uji": total(c.pct_change().fillna(0).iloc[batas:]), "mulai_uji": df.index[batas]}
    if not (m["ret_uji"] > 0 and m["sharpe_uji"] > 0):
        m["tingkat"] = "gagal"
    elif m["bh_uji"] > 0 and m["ret_uji"] < 0.5 * m["bh_uji"]:
        m["tingkat"] = "tipis"
    else:
        m["tingkat"] = "lulus"
    return m

# ---------- AREA DEMAND DAN SUPPLY ----------
def titik_balik(seri, cari_terendah):
    w = 2 * AREA_JENDELA + 1
    batas = seri.rolling(w, center=True).min() if cari_terendah else seri.rolling(w, center=True).max()
    out, terakhir = [], -999
    for i in np.flatnonzero((seri == batas).to_numpy()):
        if i - terakhir > AREA_JENDELA:
            out.append((float(seri.iloc[i]), int(i)))
        terakhir = i
    return out

def bikin_rencana(df, harga, a):
    d = df.iloc[-AREA_LILIN:]
    titik = sorted(titik_balik(d["Low"], True) + titik_balik(d["High"], False))
    lebar = max(a, harga * 0.015)
    grup_semua, grup = [], []
    for p, i in titik:
        if grup and p - grup[0][0] > lebar:
            grup_semua.append(grup)
            grup = []
        grup.append((p, i))
    if grup:
        grup_semua.append(grup)
    area = [{"bawah": min(p for p, _ in g) - 0.25 * a, "atas": max(p for p, _ in g) + 0.25 * a,
             "sentuh": len(g), "lalu": len(d) - 1 - max(i for _, i in g)} for g in grup_semua]
    area = [z for z in area if z["bawah"] > 0]
    calon_d = [z for z in area if z["bawah"] <= harga]
    demand = max(calon_d, key=lambda z: z["atas"]) if calon_d else None
    lantai = max(harga, demand["atas"]) if demand else harga
    calon_s = [z for z in area if z["bawah"] > lantai]
    supply = min(calon_s, key=lambda z: z["bawah"]) if calon_s else None
    if demand:
        beli_bawah, beli_atas = bulat(demand["bawah"]), bulat(demand["atas"])
        stop = bulat(max(demand["bawah"] - AREA_BUFFER * a, harga * 0.01))
        masuk = min(harga, beli_atas)
        posisi = "di" if harga <= beli_atas * 1.01 else "dekat" if harga <= beli_atas + a else "jauh"
    else:
        beli_bawah = beli_atas = bulat(harga)
        stop, masuk, posisi = bulat(max(harga - STOP_ATR * a, harga * 0.01)), harga, ""
    if stop >= masuk:
        stop = bulat(masuk * 0.95)
    batas_target = masuk + TARGET_MAKS_ATR * a
    if supply and supply["bawah"] <= batas_target:
        target, asal = bulat(supply["bawah"]), "supply"
    elif supply:
        target, asal = bulat(batas_target), "dibatasi"
    else:
        target, asal = bulat(masuk + TARGET_ATR * a), "atr"
    if target <= masuk:
        target = bulat(masuk * 1.005)
    rr = (target - masuk) / (masuk - stop) if masuk > stop else 0.0
    return {"demand": demand, "supply": supply, "beli_bawah": beli_bawah, "beli_atas": beli_atas,
            "masuk": float(masuk), "stop": stop, "target": target, "asal_target": asal,
            "rr": float(max(rr, 0)), "posisi_area": posisi}

# ---------- ANALISA SATU KOIN ----------
def label(skor):
    if skor >= 70: return LABEL[0]
    if skor >= 55: return LABEL[1]
    if skor >= 40: return LABEL[2]
    return LABEL[3]

def proses(kode, df, btc_sehat, volume):
    c, v = df["Close"], df["Volume"]
    harga = float(c.iloc[-1])
    m = belajar(df)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    akumulasi = bool(obv.iloc[-1] > obv.rolling(20).mean().iloc[-1])
    skor = 50 + 25 * m["suara"] * BOBOT_UJI[m["tingkat"]]
    skor += 12 if harga > c.rolling(200).mean().iloc[-1] else -12
    skor += 8 if harga > c.iloc[-43] else -8          # momentum 7 hari
    skor += 5 if akumulasi else -5
    if btc_sehat is False:
        skor -= 15
    skor = float(np.clip(skor, 0, 100))
    vol = volume.get(kode)
    likuid = vol is None or vol >= MIN_LIKUID * 1e6
    sinyal = "Beli" if m["suara"] > 0.05 else "Jual" if m["suara"] < -0.05 else "Netral"
    d = {"kode": kode, "df": df, "m": m, "skor": skor, "harga": harga, "akumulasi": akumulasi,
         "vol": vol, "likuid": likuid, "ramai": vol is not None and vol >= RAMAI * 1e6,
         "rekom": label(skor) if likuid else LABEL[4], "sinyal": sinyal}
    d.update(bikin_rencana(df, harga, float(atr(df).iloc[-1])))
    return d

# ---------- REKAM JEJAK ----------
def perbarui(r, df, btc):
    sinyal = pd.Timestamp(r["waktu_sinyal"])
    sesudah = df[df.index > sinyal]
    if len(sesudah) == 0:
        return
    i_masuk, masuk = None, None
    if r.get("masuk") is None:
        i_masuk, masuk = 0, float(sesudah["Open"].iloc[0])
    else:
        for i in range(min(len(sesudah), TUNGGU_LILIN)):
            b = sesudah.iloc[i]
            if b["Open"] <= r["masuk"]:
                i_masuk, masuk = i, float(b["Open"])
                break
            if b["Low"] <= r["masuk"]:
                i_masuk, masuk = i, float(r["masuk"])
                break
        if i_masuk is None:
            if len(sesudah) >= TUNGGU_LILIN:
                r["status"] = "batal"
            return
    if masuk <= 0:
        return
    r["status"] = "berjalan"
    r["waktu_masuk"] = sesudah.index[i_masuk].strftime(FMT)
    r["harga_masuk"] = masuk
    pegang = sesudah.iloc[i_masuk:]
    for i, (waktu, b) in enumerate(pegang.iterrows()):
        keluar, status = None, None
        buka = masuk if i == 0 else float(b["Open"])
        if b["Low"] <= r["stop"]:
            keluar, status = min(buka, float(r["stop"])), "stop"
        elif i > 0 and b["High"] >= r["target"]:
            keluar, status = max(buka, float(r["target"])), "target"
        elif i + 1 >= MAKS_LILIN:
            keluar, status = float(b["Close"]), "waktu"
        if keluar is not None:
            r.update(status=status, waktu_keluar=waktu.strftime(FMT), harga_keluar=keluar, lilin=i + 1,
                     hasil=keluar / masuk - 1 - BIAYA_BELI - BIAYA_JUAL)
            r["btc"] = None
            if btc is not None:
                a, z = btc["Close"].asof(pd.Timestamp(r["waktu_masuk"])), btc["Close"].asof(waktu)
                if pd.notna(a) and pd.notna(z) and a > 0:
                    r["btc"] = float(z / a - 1)
            r.pop("hasil_sementara", None)
            return
    r["lilin"] = len(pegang)
    r["hasil_sementara"] = float(pegang["Close"].iloc[-1]) / masuk - 1

# ---------- HALAMAN ----------
CSS = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .peringatan{background:#fff3cd;padding:8px 12px;border-radius:8px;font-size:.92rem}
.modal-box{background:#eef5ff;border:1px solid #b9d3f5;border-radius:8px;padding:12px;margin:16px 0}
.modal-box label{display:inline-block;margin:4px 16px 4px 0;font-weight:600}
.modal-box input{font-size:1rem;padding:6px 8px;border:1px solid #999;border-radius:6px;width:150px}
.modal-box input#risiko{width:60px} #modal-teks{font-weight:700;color:#0b5cad}
.filter{background:#f3f3f3;border:1px solid #ddd;border-radius:8px;padding:10px;margin:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.filter input[type=text],.filter select,.filter button{font-size:.95rem;padding:6px 8px;border:1px solid #999;border-radius:6px;background:#fff}
.filter label{font-size:.9rem} #f-jumlah{width:100%;font-size:.9rem;color:#555}
.kartu-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.kartu{background:#fff;border:1px solid #ddd;border-left:5px solid #2e9e4f;border-radius:8px;padding:12px}
.kartu.lemah{border-left-color:#e08a00} .kartu .kode{font-size:1.2rem;font-weight:700}
.kartu .skor{float:right;color:#2e9e4f;font-weight:700}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem;margin:8px 0}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th{background:#f0f0f0} th:first-child,td:first-child,th.kiri,td.kiri{text-align:left}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function gambar(kode){
  var d=DATA[kode];
  if(!d||d.sudah||typeof Plotly==='undefined'){return;}
  d.sudah=true;
  var n=d.t.length, awal=Math.max(0,n-120);
  var jejak=[
    {type:'candlestick',x:d.t,open:d.o,high:d.h,low:d.l,close:d.c,name:'Harga',
     increasing:{line:{color:'#2e9e4f'}},decreasing:{line:{color:'#d64545'}}},
    {type:'scatter',mode:'lines',x:d.t,y:d.m50,name:'MA50',line:{dash:'dot'}},
    {type:'scatter',mode:'lines',x:d.t,y:d.m200,name:'MA200',line:{dash:'dot'}},
    {type:'scatter',mode:'markers',name:'Sinyal beli',x:d.b.map(function(i){return d.t[i];}),y:d.b.map(function(i){return d.c[i];}),
     marker:{symbol:'triangle-up',size:12,color:'green'}},
    {type:'scatter',mode:'markers',name:'Sinyal jual',x:d.j.map(function(i){return d.t[i];}),y:d.j.map(function(i){return d.c[i];}),
     marker:{symbol:'triangle-down',size:12,color:'red'}}
  ];
  var bentuk=[];
  function pita(z,warna){if(z){bentuk.push({type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:z[0],y1:z[1],fillcolor:warna,opacity:0.15,line:{width:0}});}}
  function garis(y,warna){bentuk.push({type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:y,y1:y,line:{color:warna,dash:'dot'}});}
  pita(d.dm,'green'); pita(d.sp,'red'); garis(d.st,'red'); garis(d.tg,'green');
  var bawah=Math.min.apply(null,d.l.slice(awal).concat([d.st]))*0.97;
  var atas=Math.max.apply(null,d.h.slice(awal).concat([d.tg]))*1.03;
  Plotly.newPlot('g-'+kode,jejak,{height:460,margin:{l:70,r:10,t:20,b:30},legend:{orientation:'h'},shapes:bentuk,
    annotations:[
      {xref:'paper',x:1,yref:'y',y:d.st,text:'Stop loss',showarrow:false,xanchor:'right',yanchor:'top'},
      {xref:'paper',x:1,yref:'y',y:d.tg,text:'Target',showarrow:false,xanchor:'right',yanchor:'bottom'}],
    xaxis:{rangeslider:{visible:false},range:[d.t[awal],d.t[n-1]]},yaxis:{range:[bawah,atas]}},
    {responsive:true,displayModeBar:false});
  var tm=d.t.slice(n-d.mg.length);
  var warna=d.mh.map(function(v){return v>=0?'#2e9e4f':'#d64545';});
  Plotly.newPlot('m-'+kode,[
    {type:'bar',x:tm,y:d.mh,name:'Histogram',marker:{color:warna}},
    {type:'scatter',mode:'lines',x:tm,y:d.mg,name:'MACD',line:{color:'#0b5cad'}},
    {type:'scatter',mode:'lines',x:tm,y:d.ms,name:'Sinyal',line:{color:'#e08a00'}}],
    {height:220,margin:{l:70,r:10,t:10,b:30},legend:{orientation:'h'}},{responsive:true,displayModeBar:false});
}
document.querySelectorAll('details.blok').forEach(function(el){
  el.addEventListener('toggle',function(){if(el.open){gambar(el.id);}});
});
function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.style.display='';el.open=true;gambar(el.id);el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();

function ambilNilai(id){return document.getElementById(id).value;}
function saring(){
  var cari=ambilNilai('f-cari').toUpperCase().split(/[ ,]+/).filter(function(k){return k;});
  var rekom=ambilNilai('f-rekom'), sinyal=ambilNilai('f-sinyal'), area=ambilNilai('f-area');
  var lulus=document.getElementById('f-lulus').checked, rrOk=document.getElementById('f-rr').checked;
  var ramai=document.getElementById('f-ramai').checked;
  var tampil={}, n=0, semua=0;
  document.querySelectorAll('tr.baris').forEach(function(tr){
    semua++;
    var d=tr.dataset;
    var ok=(cari.length===0||cari.some(function(k){return d.kode.indexOf(k)>=0;}))
      &&(!rekom||d.rekom===rekom)&&(!sinyal||d.sinyal===sinyal)&&(!area||d.area===area)
      &&(!rrOk||parseFloat(d.rr)>=RR_MIN_NILAI)&&(!lulus||d.lulus==='1')&&(!ramai||d.ramai==='1');
    tr.style.display=ok?'':'none'; tampil[d.kode]=ok; if(ok){n++;}
  });
  document.querySelectorAll('details.blok').forEach(function(el){el.style.display=tampil[el.id]===false?'none':'';});
  document.getElementById('f-jumlah').textContent='Menampilkan '+n+' dari '+semua+' koin';
}
document.getElementById('f-cari').addEventListener('input',saring);
['f-rekom','f-sinyal','f-area','f-lulus','f-rr','f-ramai'].forEach(function(id){document.getElementById(id).addEventListener('change',saring);});

var kotakModal=document.getElementById('modal'), kotakRisiko=document.getElementById('risiko');
function ribuan(x){return Math.round(x).toLocaleString('id-ID');}
function hitung(){
  var modal=parseFloat(kotakModal.value.replace(/[^0-9]/g,''))||0;
  var risiko=parseFloat(kotakRisiko.value.replace(',','.'))||0;
  document.getElementById('modal-teks').textContent='= Rp '+ribuan(modal)+', maks rugi Rp '+ribuan(modal*risiko/100)+' per transaksi';
  try{localStorage.setItem('modal_kripto',modal);localStorage.setItem('risiko_kripto',risiko);}catch(err){}
  document.querySelectorAll('[data-harga]').forEach(function(el){
    var h=parseFloat(el.dataset.harga), s=parseFloat(el.dataset.stop), nilai=0;
    if(h>s&&h>0){nilai=Math.min(modal*risiko/100/((h-s)/h),modal);}
    el.textContent='Rp '+ribuan(nilai)+' (sekitar '+(nilai/h).toPrecision(4)+' '+el.dataset.kode+')';
  });
}
try{
  var m0=localStorage.getItem('modal_kripto'), r0=localStorage.getItem('risiko_kripto');
  if(m0&&parseFloat(m0)>0){kotakModal.value=m0;}
  if(r0&&parseFloat(r0)>0){kotakRisiko.value=r0;}
}catch(err){}
kotakModal.addEventListener('input',hitung);
kotakRisiko.addEventListener('input',hitung);
hitung(); saring();
"""

def daftar(seri):
    return [None if pd.isna(v) else float(np.format_float_positional(float(v), precision=6, unique=False,
            fractional=False, trim="-")) for v in seri]

def data_grafik(d):
    df, m = d["df"], d["m"]
    k = df.iloc[-GRAFIK_LILIN:]
    c = df["Close"]
    ubah = m["pos"].diff().reindex(k.index).to_numpy()
    garis, sinyal, hist = info_macd(c)
    return {"t": [x.strftime(FMT) for x in k.index],
            "o": daftar(k["Open"]), "h": daftar(k["High"]), "l": daftar(k["Low"]), "c": daftar(k["Close"]),
            "m50": daftar(c.rolling(50).mean().reindex(k.index)),
            "m200": daftar(c.rolling(200).mean().reindex(k.index)),
            "b": [int(i) for i in np.flatnonzero(ubah == 1)], "j": [int(i) for i in np.flatnonzero(ubah == -1)],
            "dm": [d["demand"]["bawah"], d["demand"]["atas"]] if d["demand"] else None,
            "sp": [d["supply"]["bawah"], d["supply"]["atas"]] if d["supply"] else None,
            "st": d["stop"], "tg": d["target"],
            "mg": daftar(garis.iloc[-120:]), "ms": daftar(sinyal.iloc[-120:]), "mh": daftar(hist.iloc[-120:])}

def atribut(d):
    return f"data-harga='{d['masuk']}' data-stop='{d['stop']}' data-kode='{d['kode']}'"

def area_teks(d):
    return f"{harga_teks(d['beli_bawah'])} - {harga_teks(d['beli_atas'])}" if d["demand"] else "-"

def kekuatan(z):
    lemah = " (area lemah)" if z["sentuh"] < AREA_MIN_SENTUH else ""
    return f"disentuh {z['sentuh']} kali, terakhir {z['lalu']} lilin lalu{lemah}"

def teks_ramai(d):
    if not d["likuid"]:
        return "⚠️ KURANG LIKUID di Indodax, selisih harga beli dan jual bisa lebar"
    return "ramai" if d["ramai"] else "agak sepi, cek selisih harga beli dan jual sebelum transaksi"

def blok_detail(d, btc_sehat):
    m = d["m"]
    masuk, stop, target = d["masuk"], d["stop"], d["target"]
    if d["demand"]:
        baris_beli = f"Area beli (demand): <b>{rp(d['beli_bawah'])} sampai {rp(d['beli_atas'])}</b>, " + kekuatan(d["demand"])
    else:
        baris_beli = "Area demand gak ketemu di bawah harga sekarang, jadi rencana dihitung dari harga sekarang pakai rumus ATR"
    asal = {"supply": "batas bawah area supply" + (", " + kekuatan(d["supply"]) if d["supply"] else ""),
            "dibatasi": f"dibatasi {TARGET_MAKS_ATR}x gerak per lilin, karena area supply terdekat masih jauh",
            "atr": "rumus ATR, karena gak ada area supply di atas harga"}[d["asal_target"]]
    layak = "layak" if d["rr"] >= RR_MIN else f"kurang layak, di bawah {RR_MIN:g}"
    teks_btc = {True: "sehat", False: "lemah (skor dipotong 15)", None: "data tidak tersedia"}[btc_sehat]
    if m["yakin"] <= 0:
        teks_yakin = "<b>0%</b>, gak ada satu pun strategi yang untung di data belajar, jadi suaranya dianggap netral"
    else:
        teks_yakin = f"<b>{m['yakin'] * 100:.0f}%</b> (nilai belajar terbaik {m['terbaik']:+.2f}, dipercaya penuh kalau {YAKIN_PENUH:g} ke atas)"
    baris = "".join(f"<tr><td>{e(t['nama'])}</td><td>{t['sharpe_latih']:+.2f}</td><td>{t['bobot'] * 100:.0f}%</td>"
                    f"<td>{t['ret_uji'] * 100:+.1f}%</td><td>{'Beli' if t['posisi'] == 1 else 'Di luar'}</td></tr>"
                    for t in m["tabel"])
    return f"""<details class="blok" id="{d['kode']}">
<summary><b>{d['kode']}</b> · {d['rekom']} · skor {d['skor']:.0f}</summary>
<div id="g-{d['kode']}"></div><div id="m-{d['kode']}"></div>
<p class="info"><a href="https://indodax.com/market/{d['kode']}IDR" target="_blank" rel="noopener">Buka {d['kode']}/IDR di Indodax ↗</a></p>
<p><b>Rencana transaksi</b></p>
<ul>
<li>Harga sekarang {rp(d['harga'])} · <b>{TEKS_AREA[d['posisi_area']]}</b></li>
<li>{baris_beli}</li>
<li>Stop loss <b>{rp(stop)}</b> ({(stop / masuk - 1) * 100:+.1f}% dari harga masuk acuan {rp(masuk)})</li>
<li>Target <b>{rp(target)}</b> ({(target / masuk - 1) * 100:+.1f}%), {asal}</li>
<li>Untung banding rugi: <b>{d['rr']:.1f} banding 1</b> ({layak})</li>
<li>Maksimal beli: <span {atribut(d)}></span></li>
</ul>
<p><b>Teknikal: {d['skor']:.0f}/100</b></p>
<ul>
<li>Suara gabungan strategi: {m['suara_mentah']:+.2f} sebelum disesuaikan, <b>{m['suara']:+.2f}</b> setelah dikali kepercayaan → <b>{d['sinyal'].upper()}</b> ({m['jumlah_beli']} dari {m['jumlah']} strategi lagi posisi beli)</li>
<li>Kepercayaan ke strategi: {teks_yakin}</li>
<li>Ujian gabungan sejak {m['mulai_uji']:%d %b %Y}: {m['ret_uji'] * 100:+.1f}% vs beli-diamkan {m['bh_uji'] * 100:+.1f}% → <b>{TEKS_UJI[m['tingkat']]}</b></li>
<li>Kondisi Bitcoin: {teks_btc}</li>
<li>Arah volume: {'akumulasi' if d['akumulasi'] else 'distribusi'} · Volume 24 jam di Indodax: {vol_teks(d['vol'])} → {teks_ramai(d)}</li>
</ul>
<div class="geser"><table><tr><th>Strategi</th><th>Nilai belajar</th><th>Bobot suara</th><th>Hasil ujian</th><th>Posisi sekarang</th></tr>{baris}</table></div>
</details>"""

def kepala(judul):
    return ("<!doctype html><html lang='id'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{judul}</title><style>{CSS}</style>"
            "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js' charset='utf-8'></script></head><body>")

def bikin_halaman(hasil, btc_sehat, dilewati, sepi, waktu_lilin):
    wib = datetime.now(timezone(timedelta(hours=7)))
    b = [kepala("Ringkasan Kripto")]
    b.append("<h1>🪙 Ringkasan Kripto (Indodax, lilin 4 jam)</h1>")
    b.append("<p><a href='rekam.html'>📒 Lihat rekam jejak rekomendasi →</a></p>")
    b.append(f"<p class='info'>Lilin terakhir yang sudah selesai: {waktu_lilin:%d %b %Y %H:%M} WIB · "
             f"diperbarui {wib:%d %b %Y %H:%M} WIB · {len(hasil)} koin</p>")
    if btc_sehat is False:
        b.append("<p class='peringatan'>⚠️ Bitcoin <b>lemah</b> (di bawah rata-rata 200 hari). Semua skor dipotong 15.</p>")
    elif btc_sehat is True:
        b.append("<p>Bitcoin: <b>sehat</b> (di atas rata-rata 200 hari)</p>")
    b.append("<p class='peringatan'>Kripto jauh lebih liar dari saham. Harga bisa lompat melewati stop loss, "
             "jadi rugi nyata bisa lebih besar dari hitungan di halaman ini.</p>")
    if dilewati:
        b.append(f"<p class='info'>Dilewati (gak ada di Indodax, datanya kurang, atau gak ada transaksi): {e(', '.join(dilewati))}</p>")
    if sepi:
        b.append(f"<p class='info'>Dibuang karena volume 24 jamnya di bawah Rp {MIN_TAMPIL} juta: {e(', '.join(sepi))}</p>")
    b.append("<div class='modal-box'>"
             f"<label>Modal (Rp) <input id='modal' type='text' inputmode='numeric' value='{MODAL}'></label>"
             f"<label>Maks rugi per transaksi (%) <input id='risiko' type='text' inputmode='decimal' value='{RISIKO}'></label>"
             "<div id='modal-teks'></div>"
             "<div class='info'>Maks beli di seluruh halaman ngikutin angka ini. Angkanya cuma kesimpen di browser lu.</div></div>")

    b.append("<h2>Top koin layak beli</h2>")
    top = [d for d in hasil if d["rekom"] == LABEL[0]][:JUMLAH_TOP]
    if top:
        b.append(f"<p class='info'>Kartu bergaris oranye = skornya bagus tapi untung banding ruginya di bawah {RR_MIN:g}.</p><div class='kartu-wrap'>")
        for d in top:
            lemah = d["rr"] < RR_MIN
            beli = f"Area beli Rp {area_teks(d)}<br>" if d["demand"] else ""
            b.append(f"<div class='kartu{' lemah' if lemah else ''}'><span class='skor'>{d['skor']:.0f}</span>"
                     f"<div class='kode'><a href='#{d['kode']}'>{d['kode']}</a></div>"
                     f"Harga {rp(d['harga'])}<br>{beli}{TEKS_AREA[d['posisi_area']]}<br>"
                     f"Stop loss {rp(d['stop'])}<br>Target {rp(d['target'])}<br>"
                     f"{'⚠️ ' if lemah else ''}Untung banding rugi {d['rr']:.1f} : 1<br>"
                     f"Kepercayaan strategi {d['m']['yakin'] * 100:.0f}%<br>"
                     f"Volume {vol_teks(d['vol'])}<br>"
                     f"Maks beli <b><span {atribut(d)}></span></b></div>")
        b.append("</div>")
    else:
        b.append("<p>Sekarang gak ada koin yang skornya 70 ke atas. Gak beli apa-apa juga keputusan yang sah.</p>")

    b.append("<h2>Peringkat lengkap</h2>")
    pilihan = "".join(f"<option value='{e(x)}'>{e(x)}</option>" for x in LABEL)
    b.append("<div class='filter'><input id='f-cari' type='text' placeholder='Cari kode, misal BTC, SOL'>"
             f"<select id='f-rekom'><option value=''>Semua rekomendasi</option>{pilihan}</select>"
             "<select id='f-sinyal'><option value=''>Semua sinyal</option><option value='Beli'>Beli</option>"
             "<option value='Netral'>Netral</option><option value='Jual'>Jual</option></select>"
             "<select id='f-area'><option value=''>Semua posisi harga</option><option value='di'>Di area beli</option>"
             "<option value='dekat'>Dekat area beli</option><option value='jauh'>Jauh dari area beli</option></select>"
             f"<label><input type='checkbox' id='f-ramai'> Cuma yang ramai (Rp {RAMAI / 1000:g} miliar ke atas)</label>"
             "<label><input type='checkbox' id='f-lulus'> Cuma yang lulus ujian</label>"
             f"<label><input type='checkbox' id='f-rr'> U:R minimal {RR_MIN:g}</label><div id='f-jumlah'></div></div>")
    b.append("<div class='geser'><table><tr><th>Kode</th><th>Harga (Rp)</th><th>Skor</th><th class='kiri'>Rekomendasi</th>"
             "<th class='kiri'>Sinyal</th><th>Yakin</th><th class='kiri'>Area beli</th><th>Stop loss</th><th>Target</th>"
             "<th>U:R</th><th>Volume 24 jam</th></tr>")
    ikon = {"di": "✅ ", "dekat": "🟡 ", "jauh": "⏳ ", "": ""}
    for d in hasil:
        b.append(f"<tr class='baris' data-kode='{d['kode']}' data-rekom='{e(d['rekom'])}' data-sinyal='{d['sinyal']}' "
                 f"data-area='{d['posisi_area']}' data-rr='{d['rr']:.2f}' data-ramai='{'1' if d['ramai'] else '0'}' "
                 f"data-lulus='{'1' if d['m']['tingkat'] == 'lulus' else '0'}'>"
                 f"<td><a href='#{d['kode']}'><b>{d['kode']}</b></a></td><td>{harga_teks(d['harga'])}</td><td>{d['skor']:.0f}</td>"
                 f"<td class='kiri'>{d['rekom']}</td><td class='kiri'>{d['sinyal']} {d['m']['jumlah_beli']}/{d['m']['jumlah']}</td>"
                 f"<td>{d['m']['yakin'] * 100:.0f}%</td><td class='kiri'>{ikon[d['posisi_area']]}{area_teks(d)}</td>"
                 f"<td>{harga_teks(d['stop'])}</td><td>{harga_teks(d['target'])}</td><td>{d['rr']:.1f}</td>"
                 f"<td>{vol_teks(d['vol'])}</td></tr>")
    b.append("</table></div>")

    b.append("<h2>Detail per koin</h2><p class='info'>Klik buat buka grafik lilin 4 jam dan penjelasannya. "
             "Pita hijau = area demand, pita merah = area supply.</p>")
    for d in hasil:
        b.append(blok_detail(d, btc_sehat))
    b.append("<footer>Bukan saran investasi. Performa masa lalu tidak menjamin hasil ke depan. Data dari Indodax.</footer>")
    b.append("<script>var DATA=" + json.dumps({d["kode"]: data_grafik(d) for d in hasil}, separators=(",", ":")) + ";</script>")
    b.append("<script>" + JS.replace("RR_MIN_NILAI", str(RR_MIN)) + "</script></body></html>")
    return "\n".join(b)

def baris_ringkas(nama, rek, batal):
    n = len(rek)
    if n == 0:
        return f"<tr><td>{e(nama)}</td><td>0</td><td>{batal}</td>" + "<td>-</td>" * 6 + "</tr>"
    bagian = lambda st: f"{sum(1 for r in rek if r['status'] == st) / n * 100:.0f}%"
    rata = float(np.mean([r["hasil"] for r in rek]))
    ada = [r["btc"] for r in rek if r.get("btc") is not None]
    btc = float(np.mean(ada)) if ada else None
    jumlah = str(n) if n >= MIN_BERARTI else f"{n} ⚠️"
    return (f"<tr><td>{e(nama)}</td><td>{jumlah}</td><td>{batal}</td><td>{bagian('target')}</td><td>{bagian('stop')}</td>"
            f"<td>{bagian('waktu')}</td><td><b>{pct(rata)}</b></td><td>{pct(btc)}</td>"
            f"<td><b>{pct(None if btc is None else rata - btc)}</b></td></tr>")

def bikin_rekam(rekam):
    wib = datetime.now(timezone(timedelta(hours=7)))
    selesai = [r for r in rekam if r["status"] in SELESAI]
    batal = [r for r in rekam if r["status"] == "batal"]
    jalan = [r for r in rekam if r["status"] == "berjalan"]
    tunggu = [r for r in rekam if r["status"] == "menunggu"]
    b = [kepala("Rekam Jejak Kripto"), "<h1>📒 Rekam Jejak Kripto</h1>",
         "<p><a href='index.html'>← Kembali ke ringkasan</a></p>",
         f"<p class='info'>Diperbarui {wib:%d %b %Y %H:%M} WIB · {len(selesai)} selesai · {len(jalan)} berjalan · "
         f"{len(tunggu)} menunggu masuk · {len(batal)} batal</p>",
         f"<p class='peringatan'>Aturan: pasang beli di batas atas area beli, tunggu maksimal {TUNGGU_LILIN * 4} jam. "
         f"Setelah masuk, keluar saat kena target, kena stop loss, atau setelah {MAKS_LILIN * 4 // 24} hari. "
         f"Hasil sudah dipotong biaya {(BIAYA_BELI + BIAYA_JUAL) * 100:.2f}%. Tanda ⚠️ = catatannya belum sampai {MIN_BERARTI}.</p>"]
    kepala_tabel = ("<tr><th>{}</th><th>Selesai</th><th>Batal</th><th>Kena target</th><th>Kena stop loss</th>"
                    "<th>Waktu habis</th><th>Rata-rata hasil</th><th>Bitcoin periode sama</th><th>Selisih</th></tr>")
    b.append("<h2>Hasil per rekomendasi</h2><div class='geser'><table>" + kepala_tabel.format("Rekomendasi awal"))
    for nama in LABEL[:4]:
        b.append(baris_ringkas(nama, [r for r in selesai if r["rekom"] == nama], sum(1 for r in batal if r["rekom"] == nama)))
    b.append("</table></div><h2>Hasil per rentang skor</h2><div class='geser'><table>" + kepala_tabel.format("Skor awal"))
    for bawah, atas, nama in [(80, 101, "80 ke atas"), (70, 80, "70 sampai 79"), (60, 70, "60 sampai 69"),
                              (50, 60, "50 sampai 59"), (40, 50, "40 sampai 49"), (0, 40, "Di bawah 40")]:
        b.append(baris_ringkas(nama, [r for r in selesai if bawah <= r["skor"] < atas],
                               sum(1 for r in batal if bawah <= r["skor"] < atas)))
    b.append("</table></div><h2>Masih berjalan</h2>")
    if jalan:
        b.append("<div class='geser'><table><tr><th>Kode</th><th class='kiri'>Rekomendasi awal</th><th>Skor</th><th>Masuk</th>"
                 "<th>Harga masuk</th><th>Stop loss</th><th>Target</th><th>Lilin ke</th><th>Hasil sementara</th></tr>")
        for r in sorted(jalan, key=lambda r: r.get("hasil_sementara", 0), reverse=True):
            b.append(f"<tr><td><b>{r['kode']}</b></td><td class='kiri'>{e(r['rekom'])}</td><td>{r['skor']}</td>"
                     f"<td>{r['waktu_masuk']}</td><td>{harga_teks(r['harga_masuk'])}</td><td>{harga_teks(r['stop'])}</td>"
                     f"<td>{harga_teks(r['target'])}</td><td>{r.get('lilin', 0)}</td><td>{pct(r.get('hasil_sementara'))}</td></tr>")
        b.append("</table></div>")
    else:
        b.append("<p class='info'>Belum ada posisi yang masuk.</p>")
    b.append("<h2>50 catatan selesai terakhir</h2>")
    if selesai:
        b.append("<div class='geser'><table><tr><th>Kode</th><th class='kiri'>Rekomendasi awal</th><th>Skor</th><th>Masuk</th>"
                 "<th>Keluar</th><th class='kiri'>Akhirnya</th><th>Hasil</th><th>Bitcoin</th></tr>")
        for r in sorted(selesai, key=lambda r: r["waktu_keluar"], reverse=True)[:50]:
            b.append(f"<tr><td><b>{r['kode']}</b></td><td class='kiri'>{e(r['rekom'])}</td><td>{r['skor']}</td>"
                     f"<td>{r['waktu_masuk']}</td><td>{r['waktu_keluar']}</td><td class='kiri'>{NAMA_STATUS[r['status']]}</td>"
                     f"<td><b>{pct(r['hasil'])}</b></td><td>{pct(r.get('btc'))}</td></tr>")
        b.append("</table></div>")
    else:
        b.append("<p class='info'>Belum ada catatan yang selesai.</p>")
    b.append("<footer>Ini simulasi di atas kertas, bukan transaksi beneran. Bukan saran investasi.</footer></body></html>")
    return "\n".join(b)

# ---------- JALANKAN ----------
def utama():
    daftar_kode = baca_daftar()
    volume = ambil_volume()
    lilin, dilewati, sepi = {}, [], []
    for kode in daftar_kode:
        vol = volume.get(kode)
        if kode != "BTC" and vol is not None and vol < MIN_TAMPIL * 1e6:
            sepi.append(kode)
            continue
        print("Ambil", kode)
        df = ambil_lilin(kode)
        if df is None:
            dilewati.append(kode)
        else:
            lilin[kode] = df
        time.sleep(0.3)
    if not lilin:
        print("Semua koin gagal diambil. Halaman lama dibiarkan.")
        sys.exit(1)
    btc = lilin.get("BTC")
    btc_sehat = None
    if btc is not None and len(btc) >= 1200:
        btc_sehat = bool(btc["Close"].iloc[-1] > btc["Close"].rolling(1200).mean().iloc[-1])

    hasil = []
    for kode, df in lilin.items():
        try:
            hasil.append(proses(kode, df, btc_sehat, volume))
        except Exception as err:
            print("Gagal analisa", kode, err)
            dilewati.append(kode)
    if not hasil:
        print("Gak ada koin yang berhasil dianalisa.")
        sys.exit(1)
    hasil.sort(key=lambda d: d["skor"], reverse=True)
    waktu_lilin = max(d["df"].index[-1] for d in hasil)

    os.makedirs("docs", exist_ok=True)
    rekam = []
    if os.path.exists("docs/rekam.json"):
        try:
            with open("docs/rekam.json", encoding="utf-8") as fh:
                rekam = json.load(fh)
        except Exception:
            rekam = []
    terbuka = {r["kode"] for r in rekam if r["status"] in TERBUKA}
    for d in hasil:
        if d["kode"] in terbuka or not d["likuid"]:
            continue
        rekam.append({"kode": d["kode"], "waktu_sinyal": d["df"].index[-1].strftime(FMT), "rekom": d["rekom"],
                      "skor": int(round(d["skor"])), "harga_sinyal": d["harga"],
                      "masuk": d["masuk"] if d["demand"] else None,
                      "stop": d["stop"], "target": d["target"], "status": "menunggu"})
    for r in rekam:
        if r["status"] in TERBUKA and r["kode"] in lilin:
            perbarui(r, lilin[r["kode"]], btc)

    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(hasil, btc_sehat, dilewati, sepi, waktu_lilin))
    with open("docs/rekam.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_rekam(rekam))
    with open("docs/rekam.json", "w", encoding="utf-8") as fh:
        json.dump(rekam, fh, ensure_ascii=False)
    print("Selesai:", len(hasil), "koin,", len(dilewati), "dilewati,", len(sepi), "terlalu sepi,", len(rekam), "catatan")

utama()
