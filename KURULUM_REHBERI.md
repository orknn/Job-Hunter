# 🎯 Job Hunter — Kurulum Rehberi

## 🔑 Neden Şifreleri Kodun İçine Yazmıyoruz?

Kodlarımızın içine API şifrelerini yazarsan ve bu kodu GitHub'a yüklersen, kötü niyetli botlar 5 dakika içinde o şifreleri bulup senin adına binlerce dolarlık işlem yapabilirler. Bu yüzden şifreleri kodun içinde saklamak çok büyük bir güvenlik riskidir!

Şifreleri **GitHub'ın "Secrets" (Gizli Kasa)** bölümüne koymamız gerekiyor. Kodlar çalışırken şifreyi o kasadan güvenle kendisi çekecek.

Lütfen aşağıdaki adımları sırasıyla uygula:

---

## 📤 Adım 1: Kodu GitHub'a Push'la

Terminali (VS Code içindeki terminal) aç ve şu komutu çalıştır:

```bash
git push -u origin main
```

*(Eğer hata verirse `git pull origin main --rebase` komutunu çalıştırıp sonra tekrar push'la).*

---

## 🔑 Adım 2: API Key'leri GitHub Secrets'a Ekle (ÇOK ÖNEMLİ)

1. Tarayıcında şu adrese git: **[github.com/orknn/Job-Hunter/settings/secrets/actions](https://github.com/orknn/Job-Hunter/settings/secrets/actions)**
2. Sayfanın sağ üst köşesindeki yeşil **"New repository secret"** butonuna tıkla.
3. Şimdi her bir şifreyi tek tek ekleyeceğiz:

### 1️⃣ OpenAI API Şifresi (`OPENAI_API_KEY`)
- **Name (İsim)** alanına yaz: `OPENAI_API_KEY`
- **Secret (Değer)** alanına: `sk-ant-api03...` ile başlayan o uzun şifreni yapıştır.
- **"Add secret"** butonuna bas.

### 2️⃣ Adzuna App ID (`ADZUNA_APP_ID`)
- **Name:** `ADZUNA_APP_ID`
- **Secret:** Adzuna'dan email ile gelen App ID'yi yapıştır
- **"Add secret"** butonuna bas.

### 3️⃣ Adzuna App Key (`ADZUNA_APP_KEY`)
- **Name:** `ADZUNA_APP_KEY`
- **Secret:** Adzuna'dan email ile gelen App Key'i yapıştır
- **"Add secret"** butonuna bas.

### 4️⃣ Gmail Adresin (`GMAIL_USERNAME`)
- **Name:** `GMAIL_USERNAME`
- **Secret:** `bicenorkun@gmail.com`
- **"Add secret"** butonuna bas.

### 5️⃣ Gmail Uygulama Şifresi (`GMAIL_APP_PASSWORD`)
*(Dikkat: Bu senin normal Gmail şifren DEĞİLDİR!)*
- **Name:** `GMAIL_APP_PASSWORD`
- **Secret:** 16 harfli Google Uygulama Şifreni buraya yapıştır.
- **"Add secret"** butonuna bas.

> **Nasıl alırım?** 
> 1. [myaccount.google.com/security](https://myaccount.google.com/security) adresine git.
> 2. **2 Adımlı Doğrulama** açık olmalı.
> 3. Aynı sayfada **"App passwords"** / **"Uygulama şifreleri"** araması yap.
> 4. Uygulama ismine `job-hunter` yazıp oluştur de. 
> 5. Çıkan 16 harfli sarı şifreyi kopyalayıp buraya yapıştır.

---

## 🧪 Adım 3: Çalıştır ve Test Et!

Beş şifreyi de ekledikten sonra sistemi test edebiliriz:

1. Şu adrese git: [github.com/orknn/Job-Hunter/actions](https://github.com/orknn/Job-Hunter/actions)
2. Sol taraftaki menüden **"Weekly Job Digest"** seçeneğine tıkla.
3. Sağ tarafta çıkan mavi **"Run workflow"** butonuna bas ve tekrar yeşil **"Run workflow"** diyerek çalıştır.
4. Sarı renkli ikon belirecek, işlemin bitmesini (yaklaşık 1-2 dakika) bekle. 
5. İşlem bitince Gmail hesabını kontrol et! 🎯
