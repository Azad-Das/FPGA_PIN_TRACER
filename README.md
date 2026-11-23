# FPGA Pin Tracer

PCB şematik PDF'lerinden FPGA'ya giden bağlantıları çıkaran masaüstü uygulaması.

Bir veya daha fazla şematik PDF'i verirsiniz; uygulama her çevre birimi (sensör, ADC, DAC,
Ethernet PHY, SPI/I2C/UART cihazı, konektör…) için hangi pininin FPGA'nın hangi pinine ve
bankasına gittiğini, aradaki seri direnç, seviye çevirici ve kartlar arası konektörlerle
birlikte gösterir.

```
SENSOR / U5 (TMP117) / SCL
   ↳ R12 (seri direnç 33)
   ↳ U7 (TXS0102) A2→B2
   ↳ SENSOR/J2.H8 ⇄ BACKPLANE/J10.H8
   ↳ BACKPLANE/J11.H8 ⇄ FPGA/J1.H8
   →  FPGA / U1 pin AF12 [IO_L1N_T0_65], Bank 65 (VCC1V8)
```

## Çalıştırma

**Windows:** repoyu klonlayın veya çekin, `run.bat` dosyasına çift tıklayın.

- İlk çalıştırmada proje klasöründe ayrı bir Python ortamı (`.venv`) kurulur, 1–2 dakika sürer.
  Sonraki açılışlar anında olur. Sisteme kalıcı bir şey kurulmaz.
- Gereken: Python 3.10 veya üstü ([python.org](https://www.python.org/downloads/)).

```powershell
git clone https://github.com/Azad-Das/FPGA_PIN_TRACER.git
```

Komut satırından (GUI'siz) kullanım:

```
.venv\Scripts\python -m fpga_pin_tracer.cli kart.pdf --list
.venv\Scripts\python -m fpga_pin_tracer.cli kart.pdf --component U12 --txt
.venv\Scripts\python -m fpga_pin_tracer.cli fpga.pdf sensor.pdf --auto-mate --all --csv out.csv
.venv\Scripts\python -m fpga_pin_tracer.cli fpga.pdf sensor.pdf --mate fpga:J1=sensor:J17 --component sensor:U3
```

## Uygulamada

1. **Şematik ekle…** ile bir veya birden çok PDF seçin. Kart adı dosya adıdır, çift tıklayıp değiştirebilirsiniz.
2. Birden çok kart varsa konektör eşleşmeleri otomatik önerilir, yüksek puanlılar uygulanır.
   **Konektör eşleştirme…** penceresinde kontrol edip değiştirebilir veya elle ekleyebilirsiniz.
3. Soldaki listede FPGA'ya bağlı bileşenler görünür. Arama kutusu ve tür filtresiyle bulun,
   işaretleyin. Tabloda yalnızca işaretli bileşenlerin bağlantıları görünür.
4. **Ara katmanları göster**: her satırın altında direnç, seviye çevirici ve konektör geçişlerini açar.
5. **CSV / TXT dışa aktar**: tablodaki sonuçları kaydeder (ara katmanlar açıksa onlar da yazılır).

## Nasıl çalışır?

Araç belirli bir EDA programına bağlı değildir. PDF'teki vektör çizim ve metinden bağlantıları
geometrik olarak yeniden kurar:

1. **Semboller**: dikdörtgen gövdeler. Gövde kenarına dik kısa çizgiler pindir. Pin numarası
   çizginin yanındaki küçük metin, pin adı gövde içindeki aynı hizadaki metindir.
2. **Teller**: yatay/dikey çizgiler. Uç uca değen veya bir ucu diğerinin üstünde biten (T
   bağlantı) çizgiler aynı nettir; sadece kesişen çizgiler bağlı sayılmaz.
3. **Net etiketleri**: telin üstünde duran metin. Aynı etiket PDF'in her yerinde aynı nettir.
   `NC` etiketleri bağlantısız sayılır, birleştirilmez.
4. **Gövdesiz pasifler** (zigzag direnç, kondansatör): referans (R12) ve yanındaki sembole değen tel uçlarından bulunur.
5. **Cadence/Allegro PDF'leri** farklı çizilir: her çizgi iki kez ve "kapalı yol" olarak yazılır,
   gövdeler dört ayrı çizgidir, FMC/VPX konnektörleri tek gövde yerine numaralı pin hücreleri
   yığınıdır, net adları tel ucunun yanına boşlukla yazılır. Bu tarz sayfa bazında otomatik
   tanınır ve bu kurallar yalnızca o sayfalarda uygulanır.
6. **Altium PDF'leri** her pinde görünmez işaret metni (`PI<ref>0<pin>`) taşır. Varsa pin
   eşlemesi bunlarla kesinleştirilir.
7. **Roller**: FPGA (parça numarası: XC…, 10M…, LFE… veya çok sayıda IO pini), seviye
   çevirici/buffer (TXS, TXB, SN74LVC, PCA9306…), konektör (J/P refdes, ASP-…, SEAF…), pasif.
8. **İzleme**: seçilen pinden başlayıp net üzerinden ilerler. Seri direnç, ferrit, kuplaj
   kondansatörü, seviye çevirici (A1↔B1, 1A↔1Y, SCL1↔SCL2 …) ve eşleşmiş konektörlerden geçer.
   Güç/toprak netlerinden geçmez. FPGA pinine ulaşınca durur.
9. **Bank**: gövdedeki "BANK 34" başlığından veya Xilinx pin adı son ekinden (`IO_L1P_T0_65`).
   **Bank gerilimi**: o bankın `VCCO_xx` pinlerinin bağlı olduğu net.

### Ölçülen doğruluk

ZedBoard şematiğinde (Altium, 17 sayfa) görünmez işaretler doğru cevap olarak kullanıldı
(`tools/compare_markers.py`). Genel geometrik ayrıştırıcı, işaretlere bakmadan pinlerin
%88'ini buldu, bulduklarının **%99.7'sini doğru nete** bağladı. FPGA'nın 484 pininin tamamı,
pin adlarıyla birlikte doğru çıktı. Bulunamayanlar çoğunlukla küçük diyot dizileri ve OLED
gibi FPGA izlemeyi etkilemeyen parçalar.

Cadence çıktısı olarak Analog Devices ADS10 (Virtex UltraScale+ FPGA kartı) ile AD9084
"Apollo" değerlendirme kartı birlikte denendi: FMC+ konnektörleri otomatik eşleşti (puan 0.87),
AD9084'ün 112 sinyal pini FPGA pini ve bankasına kadar izlendi (SerDes hatları, HSCI, SPI, GPIO;
aradaki seri direnç, kuplaj kondansatörü ve FXLA104 seviye çeviricileriyle birlikte).

## Kartlar arası konektör eşleştirme nasıl yapılır?

İki şematikte hangi konektörün hangisiyle mate olduğunu bulmak için her konektör çifti
(farklı kartlarda) dört bağımsız ipucuyla puanlanır (`fpga_pin_tracer/mating.py`):

1. **Parça numarası**: bilinen eş çiftler. Örn. Samtec FMC'de taşıyıcı (FPGA kartı) tarafı
   `ASP-134486-01` (HPC) / `ASP-134603-01` (LPC), takılan kart tarafı `ASP-134488-01` (HPC) /
   `ASP-134602-01` (LPC). Aynı aileden ama zıt taraftan iki parça güçlü bir işarettir.
   VPX, Samtec SEAF/SEAM, QSH/QTH, ERF8/ERM8 gibi aileler de tabloda.
2. **Ortak pin numaraları**: FMC pinleri `A1..K40`, VPX pinleri wafer/sıra düzenindedir. İki
   konektörün kullandığı pin numaraları ne kadar örtüşüyor?
3. **Sinyal adı eşleşmesi**: aynı pin numarasındaki net/pin adları normalleştirilip
   karşılaştırılır. Taşıyıcıdaki `FMC-LA01_CC_P` ile karttaki `LA01_P_CC`, önek ve `CC`
   atılınca aynı sinyaldir (`LA01_P`).
4. **Güç/toprak deseni**: GND, 3P3V, 12P0V, VADJ pinleri iki tarafta aynı numaralarda olmalı.
   Bu, yanlış eşleşmeleri eler.

Puan 0.6 ve üstüyse eşleşme otomatik uygulanır, daha düşükse yalnızca önerilir. Son karar
kullanıcıdadır. Eşleşen konektörlerde pin N ⇄ pin N kabul edilir.

Örnek: ZedBoard `J1` (FMC LPC, ASP-134603-01) ile XM105 `J17` (FMC HPC, ASP-134488-01) için
puan 0.88. Gerekçeler: eş parça numaraları, 88/88 sinyal adı eşleşmesi, 70/72 güç/toprak pini uyumu.

## Çıktı formatları

- **CSV**: `source_board, source_ref, source_part, source_pin, source_pin_name, source_net,
  fpga_board, fpga_ref, fpga_pin, fpga_pin_name, fpga_bank, bank_voltage, fpga_net, hop_count, path`.
  Excel'de Türkçe karakterler için UTF-8 BOM ile yazılır.
- **TXT**: insan okunur, bileşen başlıkları altında pin listesi ve ara katmanlar.
- **Uygulama içi tablo**: filtrelenebilir, sıralanabilir, ara katmanlar açılır kapanır.

## Şematik okunmuyorsa

Bir PDF'ten (neredeyse) hiç bağlantı çıkmazsa uygulama bir uyarıyla nedenini söyler. Ayrıntı için
PDF'i `diagnose.bat` dosyasının üzerine sürükleyin. Çıktı yalnızca sayılardan, font türlerinden ve
PDF'i üreten aracın adından oluşur; net adı, parça numarası gibi şema içeriği göstermez, bu yüzden
gizli şemalarda da paylaşılabilir. Sık nedenler: taranmış (resim) PDF, eğriye çevrilmiş yazılar,
Unicode eşlemesi olmayan fontlar.

## Bilinen sınırlamalar

- Taranmış (resim) PDF'ler desteklenmez. PDF'te vektör metin ve çizgi olmalı. Altium, OrCAD,
  Cadence, KiCad ve Mentor çıktıları genellikle böyledir.
- Veri yolları (bus) ve hiyerarşik sayfa portları özel olarak çözülmez. Netler etiket adlarıyla
  birleştirilir, bu çoğu tasarımda yeterlidir.
- Konektörlerde pin eşlemesi 1:1 kabul edilir (A1 ⇄ A1). Aynalı eşleme henüz yok.
- Diferansiyel çift arasındaki sonlandırma direnci (P–N, örn. 100 Ω) seri eleman sayılmaz.
- Tanınmayan bir seviye çevirici veya buffer'da iz durur. `roles.py` içindeki `BUFFER_PART_RE` listesine eklenebilir.

## Geliştirme

```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
.venv\Scripts\python tools\compare_markers.py samples\zedboard_schematic.pdf
```

Testler kendi küçük şematik PDF'lerini üretir (`tests/synth.py`): sensör → direnç → seviye
çevirici → FMC → arka panel → FPGA, 3 kartlı zincir.

Gerçek test için kullanılan şematikler (ZedBoard, Xilinx XM105) telifli olduğu için repoda
yoktur. `samples/` klasörüne yerel olarak konur, git tarafından yok sayılır.

## Lisans

Tüm hakları saklıdır. Repo herkese açık okunabilir/klonlanabilir; yazma yetkisi yalnızca
sahibindedir.

Kullanılan kütüphaneler: [PyMuPDF](https://pymupdf.readthedocs.io/) (AGPL-3.0 veya ticari
lisans) ve [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0). Uygulamayı paketleyip
(ör. `.exe`) başkalarına dağıtacaksanız PyMuPDF'in AGPL koşullarını göz önünde bulundurun.
