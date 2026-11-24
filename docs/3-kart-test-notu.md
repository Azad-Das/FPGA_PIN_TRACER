# 3 kartlı test notu (FPGA kartı + ana kart + Apollo kartı)

Bu not, projeyi ana kart şemasıyla birlikte test edecek kişi veya yapay zeka içindir. Amaç:
FPGA kartı ⇄ ana kart ⇄ Apollo kartı zincirinde uygulamanın bağlantıları doğru ve eksiksiz bulup
bulmadığını ölçmek ve geri bildirimi, geliştirmeye devam edecek tarafın anlayacağı biçimde getirmek.

## Önce sürümü kontrol edin

Bu not kodun tamamıyla birlikte geçerlidir; yalnızca `README.md` ve `docs/` yetmez. Test etmeden önce
repo güncel olmalı:

```powershell
git pull
git log --oneline -1
```

`tools/trace_report.py` dosyası yoksa veya `fpga_pin_tracer/mating.py` içinde `MIN_AUTO_SIGNALS`
geçmiyorsa kod eskidir; sonuçlar bu notla karşılaştırılamaz.

## İlk geri bildirimden sonra yapılanlar

İlk test (KD-0000-0193 SOM, AD-0000-3107 ana kart, AD-0000-2444 RF kartı) eski bir kodla yapıldı,
ama şu gerçek sorunları gösterdi; hepsi düzeltildi ve her biri için `tests/` altında bu kartları taklit
eden bir test var (`test_three_boards_like_som_mainboard_rf` ve diğerleri):

- Konnektör pininde tel yoksa ve sinyal adı pin ucunun birkaç punto ötesine yazılmışsa, bu ad artık o
  pinin net adı olarak alınıyor (`PageParser.pin_end_labels`).
- Pin adı pin numarasıyla aynı olan genel konnektörlerde (A14 / A14) bu "ad" artık sinyal adı eşleşmesi
  sayılmıyor; ortak sinyal pini sayısına yalnızca iki tarafta da bir yere bağlanan pinler giriyor.
- Gövdesi dört ayrı çizgiyle çizilmiş büyük semboller (LS1046 gibi) artık her sayfada bulunuyor
  (yeterince büyük ve kenarlarından pin çıkan kutular).
- Aynı tipte birden çok konnektör varsa (ana karttaki KN2/KN3/KN4 gibi) ve sinyal adları eşleşmiyorsa,
  eşit puanlı eşleşmeler "belirsiz" işaretlenir ve otomatik uygulanmaz. Bu konnektörler **elle
  eşleştirilmelidir** (uygulamada Konnektör eşleştirme penceresi, raporda `--mate`).

## Uygulama ne yapıyor (kısaca)

PCB şematik PDF'lerinden, seçilen entegrenin her pininin FPGA'nın hangi pinine ve bankasına
gittiğini, aradaki seri direnç, kuplaj kondansatörü, seviye çevirici ve kartlar arası konnektör
geçişleriyle birlikte bulur. Ayrıntı: `README.md`.

Kodun ana parçaları:

| Dosya | Görevi |
|---|---|
| `fpga_pin_tracer/extract.py` | PDF'ten metin, çizgi, dikdörtgen çıkarma. Cadence tarzı ve "görünmez metin katmanı" (SCH2PDF) sayfalarını tanır. |
| `fpga_pin_tracer/parser.py` | Sembol gövdesi, pin, tel, net etiketi, referans bulma; netlist üretme. |
| `fpga_pin_tracer/roles.py` | Bileşen rolleri (fpga/connector/buffer/passive), güç netleri, bank, direnç/buffer geçişi. |
| `fpga_pin_tracer/mating.py` | Kartlar arası konnektör eşleştirme önerileri ve puanı. |
| `fpga_pin_tracer/trace.py` | İzleme (BFS). `MAX_HOPS = 12`. |
| `tools/trace_report.py` | Bu test için rapor aracı (aşağıda). |

## Şu ana kadar doğrulananlar

- **2 kart, AD-0000-2700 (Apollo) + KD-0000-0199 (FPGA, XCVP1502):** 34 GPIO'nun hepsi FPGA pinine
  doğru eşleşti (kullanıcı tek tek kontrol etti). Aynı tipteki iki RF konnektör çifti yanlışlıkla
  eşleşiyordu; artık 8'den az ortak sinyal pini olan çiftler otomatik uygulanmıyor, yalnızca
  öneriliyor.
- **2 kart, Analog Devices ADS10 + AD9084 Apollo değerlendirme kartı (Cadence):** 35 GPIO'nun 35'i
  şemadan elle okunan sonuçla aynı. AD9084'ün 126 sinyal pini FPGA'ya ulaşıyor.
- **3 kart:** Yalnızca küçük sentetik bir testle denendi (`tests/test_synthetic.py`,
  `test_three_boards_through_backplane`, toplam 4–5 adımlık kısa yol). **Gerçek ana kart şemasıyla
  hiç denenmedi.**

## Bildirilen sorun

Kullanıcının 3 kartlı denemesinde "FPGA kartından ana karta giden bağlantılar doğru, ama ana kart
üzerinden Apollo kartına giden bağlantılar görünmüyor" gözlemi var. Olası nedenler, olasılık
sırasıyla:

1. **Yalnızca görüntü:** Tablodaki "Ara katman" sütunu tek satıra sığmayınca kesiliyor; 3 kartta ana
   kart geçişleri kesilen kısma düşer. Bir satır Apollo pininden FPGA pinine gidiyorsa, yol ana
   karttan geçmiş demektir (kart atlanamaz). Uygulamada "Ara katmanları göster" ile tam yol görülür.
2. **Apollo ⇄ ana kart eşleşmesi kurulmamış:** "Konnektör eşleştirme" penceresinde iki eşleşme
   olmalı (ana kart ⇄ FPGA kartı, ana kart ⇄ Apollo). Biri yoksa o kısım hiç izlenmez.
3. **12 adım sınırı:** `trace.py` içinde `MAX_HOPS = 12`; daha uzun yollar **uyarısız atılıyor**.
   Ana kartta seri direnç/buffer varsa 3 kartlı yol bu sınırı aşabilir.
4. **Ana kartta farklı konnektör tipleri veya farklı pin numaralandırması** (örneğin bir tarafta FMC,
   diğer tarafta VPX). Eşleşen iki konnektör arasında pin N ⇄ pin N varsayılıyor; aynalı veya farklı
   numaralandırma desteklenmiyor.
5. **Ana kart şemasının çizim tarzı** bu zamana kadar görülmemiş bir şey içeriyor olabilir.

## Kurulum

Windows'ta proje klasöründe `run.bat` bir kez çalıştırılınca `.venv` kurulur. Sonra:

```powershell
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest -q
```

Testlerin hepsi geçmeli (şu an 15).

## Test adımları

PDF dosya adları kart adı olur (uzantısız). Aşağıda `FPGA.pdf`, `ANAKART.pdf`, `APOLLO.pdf` yerine
gerçek dosya adlarını kullanın (örneğin `KD-0000-0199_130_AA.pdf`, `AD-0000-2700_130_AA.pdf`).

**1. Her PDF'in tek başına okunduğunu kontrol edin:**

```powershell
.venv\Scripts\python -m fpga_pin_tracer.diagnose FPGA.pdf ANAKART.pdf APOLLO.pdf
```

Her dosya için en alttaki `SONUÇ` ve `ÇÖZÜMLEME` satırlarına bakın. Ana kartta bileşen ve konnektör
sayısı makul olmalı; konnektörler `connector` rolüyle görünmeli.

**2. Üç kartla rapor alın.** Apollo'daki AD9084'ün referansını (AD-0000-2700'de `TD…` ile
başlıyor olabilir) `--component` ile verin:

```powershell
.venv\Scripts\python tools\trace_report.py FPGA.pdf ANAKART.pdf APOLLO.pdf --component APOLLO:TD3 --out rapor.txt
```

Raporda:
- `KONNEKTÖR ÖNERİLERİ` ve `UYGULANAN EŞLEŞMELER`: iki kartlar arası eşleşme de uygulanmış mı?
- `BİLEŞEN … FPGA'ya ulaşan N, yalnızca adım sınırı kalkınca ulaşan M, ulaşmayan K`.
  **M > 0 ise sorun 12 adım sınırıdır.**
- Her pin için `OK` (FPGA pini ve tam yol), `LIMIT` (yalnızca sınır kalkınca bulunuyor) veya `STOP`.
  `STOP` satırlarının altında aramanın nerede bittiği yazar: eşleşmesi olmayan konnektör, eşleşmiş
  konnektörde olmayan pin, geçilemeyen buffer, yolda başka entegre.

Bir eşleşme otomatik kurulmamışsa elle ekleyip tekrar alın:

```powershell
.venv\Scripts\python tools\trace_report.py FPGA.pdf ANAKART.pdf APOLLO.pdf --component APOLLO:TD3 --mate APOLLO:KN1=ANAKART:KN7
```

**3. Elle doğrulama.** Rapordaki `OK` satırlarından en az 5 sinyali (farklı bankalardan, en az biri
diferansiyel çift) şemalardan izleyin: Apollo pini → Apollo konnektör pini → ana kartta aynı pin
numarası → ana kart içinde nereye gidiyor → ana kartın FPGA kartı tarafındaki konnektör pini →
FPGA kartında aynı pin numarası → FPGA pini. Her adımdaki pin numarasını rapordakiyle karşılaştırın.

**4. Eksikler.** Şemaya göre FPGA'ya gitmesi gerekip raporda `STOP` görünen sinyalleri listeleyin.

## Geri bildirim formatı

Şu bilgiler yeterli (şema içeriğini paylaşmak gerekmiyor; aşağıdakiler sayı, referans ve pin
numarası düzeyinde):

1. `diagnose` çıktısında her kart için `SONUÇ` ve `ÇÖZÜMLEME` satırları.
2. Rapordaki `KART`, `KONNEKTÖR ÖNERİLERİ`, `UYGULANAN EŞLEŞMELER` ve `BİLEŞEN` satırları.
3. Elle doğrulanan sinyaller için tablo:

   | Sinyal | Beklenen FPGA pini | Rapordaki FPGA pini | Doğru mu |
   |---|---|---|---|

4. Yanlış veya eksik her sinyal için: şemaya göre doğru yol (kart/konnektör.pin adımlarıyla) ve
   rapordaki `STOP` bloğu ya da yanlış yol.
5. Ana kartın yapısı hakkında kısa bilgi: kaç konnektör var, tipleri (FMC, VPX, …), konnektörler arası
   bağlantı doğrudan tel mi, net etiketi mi, sayfa dışı ok mu; arada direnç/buffer var mı; iki taraftaki
   pin numaraları aynı mı.

Kodda bir hata bulunup düzeltme önerilecekse değişikliği doğrudan yapmak yerine dosya, satır ve
önerilen değişiklik olarak yazın; ayrıca hangi testle doğrulandığını belirtin. Mevcut testlerin
(`pytest`) hâlâ geçtiğini kontrol edin.

## Bilinen ve beklenen davranışlar (hata değil)

- Apollo kartındaki AD9084'ün bazı DAC pinlerinde pin adı ile net adı ters görünür (örneğin pin adı
  `DAC_B0_P`, net `DACB0_N`). Şemada gerçekten öyle bağlı.
- SYSREF gibi bazı sinyaller FPGA'ya değil saat entegresine (HMC7044) gider; `STOP` olmaları doğaldır.
- 8'den az ortak sinyal pini olan konnektör çiftleri (RF konnektörler gibi) otomatik eşleşmez,
  yalnızca önerilir.
- Kartlar arasında net adları farklı olan genel konnektörler (iki kartta farklı adlandırılmış aynı
  sinyal, şirket içi parça numarası) düşük puan alır ve yalnızca önerilir; hangi konnektörün hangisine
  takıldığını kullanıcı bilir, `--mate` ile verilmelidir. Örnek:
  `--mate KD-0000-0193_130_AA:KN2=AD-0000-3107_130_P11:KN2`.
- Bank gerilimi sütunu voltaj değil, bankı besleyen netin adıdır.
