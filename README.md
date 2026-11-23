# FPGA Pin Tracer

PCB şematik PDF'lerinden FPGA'ya giden bağlantıları çıkaran araç.

> Durum: **tasarım aşaması** — kod henüz yazılmadı. Bu belge hedefleri ve yaklaşımı tanımlar.

## Ne yapar?

Bir veya daha fazla şematik PDF'i alır ve her çevre birimi (sensör, ADC, DAC, Ethernet PHY,
SPI/I2C/UART cihazı vb.) için şunu raporlar:

```
<Kart> / <Bileşen> / <Pin adı>  →  [ara katmanlar]  →  <FPGA kartı> / FPGA <Ball> / Bank <N>
```

Örnek:

```
SENSOR_BOARD / U12 (TMP117) / SDA
   → U30 (TXS0102, 3V3→1V8 seviye çevirici) A1→B1
   → J5 (FMC HPC) pin H7  ⇄  ANAKART J2 pin H7
   → ANAKART J9 (VPX P1) pin A4  ⇄  FPGA_BOARD P1 pin A4
   → FPGA_BOARD / U1 (XCKU040) ball AE12, Bank 65 (1.8V)
```

Özet görünümde yalnızca ilk ve son satır gösterilir; ara katmanlar isteğe bağlı açılır.

## Özellikler

- Tek kart: bileşen → (seviye çevirici / buffer / seri direnç / mux) → FPGA pini + bankası
- Çoklu kart: konektörler (FMC, VPX, board-to-board, anakart) üzerinden kartlar arası izleme
- 3+ kart zinciri: kart A → anakart konektörleri → FPGA kartı
- Uzun bileşen listesinden seçim; yalnızca seçilen bileşenin bağlantıları gösterilir
- Uygulama içinde tablo görünümü, CSV ve TXT dışa aktarma

## İşleme hattı (planlanan)

1. **PDF metin çıkarımı** — Şematik PDF'ler (Altium/OrCAD/Cadence çıktıları) genellikle
   vektör metin içerir. Her metin parçası koordinatıyla birlikte okunur (PyMuPDF).
2. **Netlist kurulumu** — Net etiketleri, off-page/port referansları ve bileşen pin numaraları
   eşleştirilerek `net → [(refdes, pin)]` tablosu oluşturulur. Hiyerarşik sayfalar arası
   bağlantılar aynı net adı ve off-page sembolleri ile birleştirilir.
3. **Bileşen sınıflandırma** — Refdes ve parça numarasından rol tahmini:
   FPGA (XC*, 10M*, ...), seviye çevirici (TXS*, TXB*, SN74LVC*...), konektör (J*, P*),
   pasif (R*, C*), diğer cihazlar.
4. **İzleme** — Seçilen bileşen pininden başlayıp net grafiğinde ilerlenir. "Geçirgen"
   bileşenler (seri direnç, buffer, seviye çevirici, konektör) üzerinden pin eşlemesiyle
   devam edilir; FPGA pinine ulaşınca durulur.
5. **FPGA bank bilgisi** — Ball → bank eşlemesi Xilinx paket pin dosyalarından
   (`xc*pkg.txt`) veya şematikteki bank sembol başlıklarından alınır.

## Kartlar arası konektör eşleştirme nasıl yapılır?

İki şematikte hangi konektörün hangisiyle mate olduğunu bulmak için birkaç sinyal birlikte
puanlanır:

1. **Konektör tipi / parça numarası** — Bir taraf genellikle "header", diğeri "socket"tır.
   Örn. Samtec `ASP-134486-01` (FMC HPC, taşıyıcı tarafı) ↔ `ASP-134488-01` (FMC HPC,
   mezzanine tarafı). Bilinen eş parça numaraları tablosu tutulur. Pin sayısı aynı olmalıdır.
2. **Standart pin adlandırması** — FMC (VITA 57.1) pinleri `A1..K40` şeklinde ve sinyal adları
   standarttır (`LA00_P_CC`, `HA05_N`, `DP0_C2M_P`...). VPX (VITA 46) için `P0/P1/J1`
   wafer/sıra düzeni benzer. İki konektörde aynı standart adlar aynı pin numaralarına
   düşüyorsa güçlü bir eşleşme işaretidir.
3. **Net adı benzerliği** — Kart A'daki `FMC_LA03_P` ile kart B'deki `LA03_P` gibi, iki
   konektörün aynı pin numaralarındaki net adlarının ne kadar örtüştüğü (normalize edilmiş
   isimler, ön/son ekler atılarak) puana eklenir.
4. **Güç/toprak deseni** — GND, 3P3V, 12P0V, VADJ pinlerinin konumlarının iki tarafta
   örtüşmesi, yanlış eşleşmeleri eler.
5. **Kullanıcı onayı** — Puanlar bir eşleşme önerisi olarak gösterilir; kullanıcı onaylar,
   değiştirir veya elle eşleştirir. Belirsiz durumda (ör. iki özdeş konektör) karar her
   zaman kullanıcıya bırakılır.

Konektörlerde pin eşleme genelde 1:1'dir (A1 ↔ A1). Ters/aynalı eşleme (bazı
board-to-board konektörlerde) konektör tipi tablosunda tanımlanır.

## Çıktı formatları

- **CSV**: `source_board, source_ref, source_part, source_pin, source_net, path, fpga_board, fpga_ref, fpga_ball, fpga_bank, bank_voltage`
- **TXT**: insan okunur, yukarıdaki örnek gibi ağaç biçiminde
- **Uygulama içi tablo**: filtrelenebilir, ara katmanlar açılıp kapanabilir

## Test verisi

Xilinx/AMD değerlendirme kartı şematikleri (ör. KC705, ZCU102, VCU118 ve FMC kartları)
test için kullanılır. Telif nedeniyle PDF'ler repoya eklenmez; `samples/` klasörüne yerel
olarak konur (git tarafından yok sayılır).

## Lisans

Tüm hakları saklıdır. Repo herkese açık okunabilir/klonlanabilir; yazma yetkisi yalnızca
sahibindedir.
