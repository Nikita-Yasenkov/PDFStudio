# Releasing PDF Studio

## How it works

[`.github/workflows/build.yml`](../.github/workflows/build.yml) runs on every
push to `main`, every pull request, and on demand (**Actions → Build → Run
workflow**):

| Job | What it does |
|---|---|
| **Windows** | Runs the tests, builds `PDFStudio.exe`, runs its self-test, signs it (releases only, see below) and zips it. |
| **macOS (arm64, x86_64)** | Runs the tests, builds `PDF Studio.app` on an Apple silicon and an Intel runner, signs it (releases only, see below), runs its self-test and puts it in a disk image, which Apple notarizes. |
| **Publish release** | Only on `main`, and only when `__version__` has no release yet: publishes release `v<version>` with all three downloads. |

Every run attaches its downloads (`windows`, `macos-arm64`, `macos-x86_64`) to
the run's summary page for 30 days, so you can try a build before releasing
it. A release only goes out when every job succeeds, so all platforms always
ship together.

## Ship a new version

1. Change `__version__` in [`src/pdf_studio/__init__.py`](../src/pdf_studio/__init__.py).
   Use MAJOR.MINOR.PATCH: bug fixes bump PATCH (0.1.1 → 0.1.2), new features
   bump MINOR (0.1.2 → 0.2.0), changes that break what users rely on bump MAJOR.
2. Add a `## <version>` section to [`CHANGELOG.md`](../CHANGELOG.md), written
   for users. It becomes the release notes, followed by install steps and
   checksums. The workflow stops before building anything if it's missing.
3. Push to `main`, or merge a pull request. Watch it under **Actions**.

If a release run fails, fix the cause and push again. The version still has
no release, so the next run publishes it.

The README's download links point at `releases/latest/download/…`, so they
always serve the newest release without changes.

**Help → Check for Updates** in the app reads the same latest release from
GitHub's API. It compares the tag with the running version, shows the
CHANGELOG section as "What's new" (the notes before *Get started*), and
downloads the file for the user's computer, checked against the SHA-256 GitHub
reports. Keep the `v<version>` tags and the download names:
`tests/test_updates.py` fails if this workflow's file names change.

On Windows, the app then installs the update itself
([`integration/updater.py`](../src/pdf_studio/integration/updater.py)): it
unpacks the zip and starts the new `PDFStudio.exe --apply-update FOLDER --wait
PID [FILE…]`, which replaces the old app folder and reopens the documents.
Two rules follow:

- **Every version keeps accepting those arguments.** The version before it is
  the one that starts it.
- **Once releases are signed, keep the same publisher name.** A signed copy
  only installs updates whose signature has the same subject. Azure renews
  the certificate every few days under the same name, which is fine; changing
  the validated identity (say, from you to a company) means users update the
  first time by hand.

## Windows code signing (Azure Artifact Signing)

A signature tells Windows who published the app. It keeps Microsoft Defender's
machine-learning false positives in check, and SmartScreen warnings fade as
the signed app builds a reputation. A brand-new certificate can still see
warnings for a short while.

**Requirements and cost.** Individual developers must be in the United States
or Canada (organizations: more countries). It needs a paid Azure subscription
and costs $9.99 a month on the Basic tier, which includes 5,000 signatures.
The pipeline signs one file per release.

### One-time setup

In the [Azure portal](https://portal.azure.com):

1. **Subscription and billing.** For individual identity validation, the
   billing account must have the account type *Individual*, and its legal name
   and sold-to address must match your government ID.
2. **Register the service.** *Subscriptions* → your subscription → *Resource
   providers* → `Microsoft.CodeSigning` → *Register*.
3. **Create the signing account.** Search *Artifact Signing Accounts* →
   *Create*. Choose a region (for example *East US*, endpoint
   `https://eus.codesigning.azure.net/`) and the *Basic* tier. Write down the
   account name and the endpoint.
4. **Verify your identity.** In the account, *Access control (IAM)* → give
   yourself the *Artifact Signing Identity Verifier* role. Then *Identity
   validations* → *Individual* → *New identity* → *Public*, and follow the
   verification steps (photo ID and the Microsoft Authenticator app). This can
   take from minutes to several business days.
5. **Create the certificate profile.** *Certificate profiles* → *Create* →
   *Public Trust*. Select your completed identity validation. Write down the
   profile name.
6. **Let GitHub sign in without a password.** *Microsoft Entra ID* → *App
   registrations* → *New registration* (for example `pdf-studio-github`). In
   the new app: *Certificates & secrets* → *Federated credentials* → *Add
   credential* → *GitHub Actions deploying Azure resources*. Use organization
   `Nikita-Yasenkov`, repository `PDFStudio`, entity *Branch*, branch `main`.
7. **Allow it to sign.** Back in the signing account: *Access control (IAM)*
   → *Add role assignment* → *Artifact Signing Certificate Profile Signer* →
   select the app registration from step 6.

In GitHub, open the repository's **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `AZURE_CLIENT_ID` | App registration → *Application (client) ID* |
| Secret | `AZURE_TENANT_ID` | App registration → *Directory (tenant) ID* |
| Secret | `AZURE_SUBSCRIPTION_ID` | Your subscription's ID |
| Variable | `AZURE_SIGNING_ENDPOINT` | The endpoint from step 3 |
| Variable | `AZURE_SIGNING_ACCOUNT` | The account name from step 3 |
| Variable | `AZURE_CERTIFICATE_PROFILE` | The profile name from step 5 |

From the next release on, `PDFStudio.exe` is signed and timestamped, and the
workflow fails the release if the signature isn't valid. Until
`AZURE_SIGNING_ENDPOINT` is set, releases go out unsigned.

## macOS signing and notarization (Apple Developer ID)

Release builds are signed with your *Developer ID Application* certificate
and notarized by Apple, so macOS opens them without warnings. Without the
secrets below, the app is ad-hoc signed, and people approve it once in
**System Settings → Privacy & Security → Open Anyway**. Pull requests and
other non-release builds are never signed.

The certificate shows the name your Apple Developer account is enrolled
under: your own name for an individual account, or the LLC's legal name for
an organization account.

### 1. Developer ID Application certificate

Only the account holder can create one. At
[developer.apple.com](https://developer.apple.com/account/resources/certificates/list)
→ *Certificates* → **+** → *Developer ID Application* → profile type *G2
Sub-CA*, upload a certificate signing request (CSR), then download the
certificate.

**With a Mac:** create the CSR in *Keychain Access → Certificate Assistant →
Request a Certificate From a Certificate Authority* ("Saved to disk").
Double-click the downloaded certificate, then in *Keychain Access → My
Certificates* right-click *Developer ID Application: …* → *Export* → a `.p12`
file with a password.

**With Windows (Git Bash):** make the private key and CSR, upload the CSR,
then combine the downloaded certificate, Apple's intermediate certificate and
the key into a `.p12`:

```bash
mkdir -p ~/pdf-studio-signing && cd ~/pdf-studio-signing
MSYS_NO_PATHCONV=1 openssl req -new -newkey rsa:2048 -nodes -keyout developer_id.key \
  -out developer_id.csr -subj "/emailAddress=you@example.com/CN=Your Name/C=US"
# upload developer_id.csr, download developerID_application.cer into this folder, then:
openssl x509 -inform DER -in developerID_application.cer -out developer_id.pem
curl -sO https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer
openssl x509 -inform DER -in DeveloperIDG2CA.cer -out developer_id_ca.pem
openssl pkcs12 -export -inkey developer_id.key -in developer_id.pem \
  -certfile developer_id_ca.pem -out developer_id.p12    # with OpenSSL 3, add -legacy
```

Keep `developer_id.key` and the `.p12` somewhere safe, such as a password
manager. Anyone with them can sign software in your name.

### 2. Notarization key

In [App Store Connect](https://appstoreconnect.apple.com) → *Users and Access*
→ *Integrations* → *App Store Connect API* → *Team Keys* → **+**, with access
*Developer*. Download the `.p8` file (Apple offers it only once), and note the
*Key ID* and the *Issuer ID* shown above the list.

### 3. GitHub secrets

In **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE_P12` | The `.p12` as base64. PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("developer_id.p12")) \| Set-Clipboard`; Mac: `base64 -i developer_id.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | The `.p12` password |
| `MACOS_NOTARY_KEY` | The whole text of the `.p8` file |
| `MACOS_NOTARY_KEY_ID` | The Key ID |
| `MACOS_NOTARY_ISSUER_ID` | The Issuer ID |

The next release is then signed with hardened runtime, self-tested while
signed, notarized, and stapled, which lets Gatekeeper check it offline. The
release notes drop the *Open Anyway* step automatically. If Apple rejects a
build, the job prints Apple's list of problems.

**On a Mac, locally:** store the key once with `xcrun notarytool
store-credentials pdf-studio --key AuthKey_XXXX.p8 --key-id … --issuer …`,
then run `PDF_STUDIO_SIGN_IDENTITY="Developer ID Application: …"
PDF_STUDIO_NOTARY_PROFILE=pdf-studio scripts/build_pdf_studio_macos.sh --smoke --notarize`.

### Intel Macs

GitHub's Intel macOS runners (`macos-15-intel`) are supported until fall 2027.
After that, drop the `x86_64` build from the matrix.

## Building locally

```powershell
.\scripts\build_pdf_studio.ps1 -Smoke -Zip       # Windows: dist\PDFStudio-win64.zip
```

```bash
scripts/build_pdf_studio_macos.sh --smoke        # macOS: dist/PDFStudio-macos-<arch>.dmg
```
