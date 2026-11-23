// ClipMic Direct Mic APO (Windows Audio Processing Object)
//
// GPLv3 License — derived from Onion Board's native/directmic/obmic.cpp.
// Runs inside audiodg.exe on the user's real microphone endpoint (SFX slot).
// Reads ClipMic's 48 kHz mono float32 ring buffer (%ProgramData%\ClipMic\MicPlugin\ring.bin)
// and mixes (MODE_ADD) or replaces (MODE_REPLACE) audio into the mic stream with smooth
// crossfades, Kaiser windowed-sinc resampling for non-48 kHz mics, child APO chaining,
// and SEH fault protection so audiodg.exe never crashes.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objbase.h>
#include <sddl.h>
#include <mmreg.h>
#include <propsys.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <wchar.h>
#include <new>

// ------------------------------------------------------------------ APO definitions
typedef LONGLONG HNSTIME;

typedef enum { BUFFER_INVALID = 0, BUFFER_VALID = 1, BUFFER_SILENT = 2 } APO_BUFFER_FLAGS;
typedef enum {
    APO_CONNECTION_BUFFER_TYPE_ALLOCATED = 0,
    APO_CONNECTION_BUFFER_TYPE_EXTERNAL = 1,
    APO_CONNECTION_BUFFER_TYPE_DEPENDANT = 2
} APO_CONNECTION_BUFFER_TYPE;
typedef enum {
    APO_FLAG_NONE = 0,
    APO_FLAG_INPLACE = 1,
    APO_FLAG_SAMPLESPERFRAME_MUST_MATCH = 2,
    APO_FLAG_FRAMESPERSECOND_MUST_MATCH = 4,
    APO_FLAG_BITSPERSAMPLE_MUST_MATCH = 8,
    APO_FLAG_DEFAULT = 14
} APO_FLAG;

struct UNCOMPRESSEDAUDIOFORMAT {
    GUID guidFormatType;
    UINT32 dwSamplesPerFrame;
    UINT32 dwBytesPerSampleContainer;
    UINT32 dwValidBitsPerSample;
    FLOAT fFramesPerSecond;
    UINT32 dwChannelMask;
};

struct APO_CONNECTION_PROPERTY {
    UINT_PTR pBuffer;
    UINT32 u32ValidFrameCount;
    APO_BUFFER_FLAGS u32BufferFlags;
    UINT32 u32Signature;
};

struct IAudioMediaType;

struct APO_CONNECTION_DESCRIPTOR {
    APO_CONNECTION_BUFFER_TYPE Type;
    UINT_PTR pBuffer;
    UINT32 u32MaxFrameCount;
    IAudioMediaType *pFormat;
    UINT32 u32Signature;
};

struct APO_REG_PROPERTIES {
    CLSID clsid;
    APO_FLAG Flags;
    WCHAR szFriendlyName[256];
    WCHAR szCopyrightInfo[256];
    UINT32 u32MajorVersion;
    UINT32 u32MinorVersion;
    UINT32 u32MinInputConnections;
    UINT32 u32MaxInputConnections;
    UINT32 u32MinOutputConnections;
    UINT32 u32MaxOutputConnections;
    UINT32 u32MaxInstances;
    UINT32 u32NumAPOInterfaces;
    IID iidAPOInterfaceList[1];
};

struct APOInitBaseStruct {
    UINT32 cbSize;
    CLSID clsid;
};

struct APOInitSystemEffects {
    APOInitBaseStruct APOInit;
    IPropertyStore *pAPOEndpointProperties;
    IPropertyStore *pAPOSystemEffectsProperties;
    void *pReserved;
    IUnknown *pDeviceCollection;
};

struct APOInitSystemEffects2 {
    APOInitBaseStruct APOInit;
    IPropertyStore *pAPOEndpointProperties;
    IPropertyStore *pAPOSystemEffectsProperties;
    void *pReserved;
    IUnknown *pDeviceCollection;
    UINT nSoftwareIoDeviceInCollection;
    UINT nSoftwareIoConnectorIndex;
    GUID AudioProcessingMode;
    BOOL InitializeForDiscoveryOnly;
};

struct IAudioMediaType : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE IsCompressedFormat(BOOL *pfCompressed) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsEqual(IAudioMediaType *pIAudioType, DWORD *pdwFlags) = 0;
    virtual const WAVEFORMATEX *STDMETHODCALLTYPE GetAudioFormat() = 0;
    virtual HRESULT STDMETHODCALLTYPE GetUncompressedAudioFormat(UNCOMPRESSEDAUDIOFORMAT *p) = 0;
};

struct IAudioProcessingObjectRT : public IUnknown {
    virtual void STDMETHODCALLTYPE APOProcess(UINT32 nIn, APO_CONNECTION_PROPERTY **ppIn,
                                              UINT32 nOut, APO_CONNECTION_PROPERTY **ppOut) = 0;
    virtual UINT32 STDMETHODCALLTYPE CalcInputFrames(UINT32 u32OutputFrameCount) = 0;
    virtual UINT32 STDMETHODCALLTYPE CalcOutputFrames(UINT32 u32InputFrameCount) = 0;
};

struct IAudioProcessingObjectConfiguration : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE LockForProcess(UINT32 nIn, APO_CONNECTION_DESCRIPTOR **ppIn,
                                                     UINT32 nOut, APO_CONNECTION_DESCRIPTOR **ppOut) = 0;
    virtual HRESULT STDMETHODCALLTYPE UnlockForProcess() = 0;
};

struct IAudioProcessingObject : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Reset() = 0;
    virtual HRESULT STDMETHODCALLTYPE GetLatency(HNSTIME *pTime) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetRegistrationProperties(APO_REG_PROPERTIES **ppRegProps) = 0;
    virtual HRESULT STDMETHODCALLTYPE Initialize(UINT32 cbDataSize, BYTE *pbyData) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsInputFormatSupported(IAudioMediaType *pOpposite,
                                                             IAudioMediaType *pRequested,
                                                             IAudioMediaType **ppSupported) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsOutputFormatSupported(IAudioMediaType *pOpposite,
                                                              IAudioMediaType *pRequested,
                                                              IAudioMediaType **ppSupported) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetInputChannelCount(UINT32 *pu32ChannelCount) = 0;
};

struct IAudioSystemEffects : public IUnknown {};

static const IID IID_IAudioProcessingObject_ =
    {0xfd7f2b29, 0x24d0, 0x4b5c, {0xb1, 0x77, 0x59, 0x2c, 0x39, 0xf9, 0xca, 0x10}};
static const IID IID_IAudioProcessingObjectRT_ =
    {0x9e1d6a6d, 0xddbc, 0x4e95, {0xa4, 0xc7, 0xad, 0x64, 0xba, 0x37, 0x84, 0x6c}};
static const IID IID_IAudioProcessingObjectConfiguration_ =
    {0x0e5ed805, 0xaba6, 0x49c3, {0x8f, 0x9a, 0x2b, 0x8c, 0x88, 0x9c, 0x4f, 0xa8}};
static const IID IID_IAudioSystemEffects_ =
    {0x5fa00f27, 0xadd6, 0x499a, {0x8a, 0x9d, 0x6b, 0x98, 0x52, 0x1f, 0xa7, 0x5b}};
static const GUID SUBTYPE_IEEE_FLOAT =
    {0x00000003, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
static const GUID SUBTYPE_PCM_ =
    {0x00000001, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
static const PROPERTYKEY PKEY_AudioEndpoint_GUID_ =
    {{0x1da5d803, 0xd492, 0x4edd, {0x8c, 0x23, 0xe0, 0xc0, 0xff, 0xee, 0x7f, 0x0e}}, 4};

#define APOERR_FORMAT_NOT_SUPPORTED ((HRESULT)0x88890003L)

// ------------------------------------------------------------------ ClipMic APO identity
// {A4E7B219-8F3C-4D6A-9E12-7B5C1D3F8A42}
static const CLSID CLSID_ClipMicAPO =
    {0xa4e7b219, 0x8f3c, 0x4d6a, {0x9e, 0x12, 0x7b, 0x5c, 0x1d, 0x3f, 0x8a, 0x42}};
static const wchar_t *STATE_KEY = L"SOFTWARE\\ClipMic\\MicPlugin\\Endpoints";
#ifndef CLIPMIC_STATE_ROOT
#define CLIPMIC_STATE_ROOT HKEY_LOCAL_MACHINE
#endif
static const uint32_t EFFECT_VERSION = 1;
static const wchar_t *APO_CLASS_KEY =
    L"SYSTEM\\CurrentControlSet\\Control\\Class\\{5989fce8-9cd0-467d-8a6a-5419e31529d4}";
static const wchar_t *EAPO_KEY = L"SOFTWARE\\EqualizerAPO\\Child APOs";
static const CLSID CLSID_EapoPreMix =
    {0xeacd2258, 0xfcac, 0x4ff4, {0xb3, 0x6d, 0x41, 0x9e, 0x92, 0x4a, 0x6d, 0x79}};
static const CLSID CLSID_EapoPostMix =
    {0xec1cc9ce, 0xfaed, 0x4822, {0x82, 0x8a, 0x82, 0xa8, 0x1a, 0x6f, 0x01, 0x8f}};

static thread_local int t_loading = 0;

// ------------------------------------------------------------------ Shared ring layout
static const uint32_t RING_MAGIC = 0x434d5043;   // "CPMC"
static const uint32_t RING_VERSION = 1;
static const uint32_t HEADER_BYTES = 4096;
static const uint32_t SLOT_OFFSET = 256;
static const uint32_t SLOTS = 16;
static const uint64_t BOARD_STALE_MS = 250;
static const uint64_t SLOT_STALE_MS = 3000;
static const double LEAD_DEFAULT_S = 0.04;
static const double LEAD_MAX_S = 0.12;
static const double FADE_S = 0.01;
static const uint64_t POS_MAX = 1ull << 52;
static const uint64_t FAULT_REST_MS = 1000;
static const int PHASES = 128;
static const double SINC_ATTEN_DB = 80.0;
static const double SINC_PASS = 0.9;
static const double PI_ = 3.14159265358979323846;

enum { MODE_ADD = 0, MODE_REPLACE = 1 };

#pragma pack(push, 1)
struct Slot {
    volatile LONG owner;           // 0
    volatile uint32_t rate;        // 4
    volatile uint32_t channels;    // 8
    volatile uint32_t flags;       // 12
    volatile uint64_t tick;        // 16
    volatile uint64_t read_pos;    // 24
    volatile uint32_t lead;        // 32
    volatile uint32_t underruns;   // 36
    volatile uint64_t blocks;      // 40
    uint8_t pad[16];               // 48 (total 64 bytes)
};

struct RingHeader {
    uint32_t magic;                // 0
    uint32_t version;              // 4
    uint32_t rate;                 // 8   48000
    uint32_t capacity;             // 12  power of two float32 frames
    volatile uint64_t write_pos;   // 16
    volatile uint64_t board_tick;  // 24  GetTickCount64 of last write
    volatile uint32_t enabled;     // 32  1 = active
    volatile uint32_t mode;        // 36  MODE_ADD / MODE_REPLACE
    volatile float gain;           // 40
    volatile float mic_gain;       // 44
    volatile uint32_t lead;        // 48  0 = default
    volatile uint32_t effect_version; // 52
    uint32_t board_pid;            // 56
    uint32_t pad0;                 // 60
};
#pragma pack(pop)

static HMODULE g_module;
static volatile LONG g_objects;
static volatile LONG g_locks;
static volatile LONG g_factoryRefs;
static volatile LONG g_ids;
static wchar_t g_dataDir[MAX_PATH];

static void initDataDir() {
    if (g_dataDir[0]) return;
    wchar_t base[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"ProgramData", base, MAX_PATH);
    if (!n || n >= MAX_PATH) wcscpy(base, L"C:\\ProgramData");
    _snwprintf(g_dataDir, MAX_PATH, L"%ls\\ClipMic\\MicPlugin", base);
    g_dataDir[MAX_PATH - 1] = 0;
}

static const LONG LOG_BURST = 40;
static const uint64_t LOG_WINDOW_MS = 10000;
static volatile LONG g_logCount;
static volatile uint64_t g_logWindow;

static void logv(bool force, const char *fmt, va_list ap) {
    if (!force) {
        uint64_t now = GetTickCount64();
        if (now - g_logWindow > LOG_WINDOW_MS) {
            g_logWindow = now;
            g_logCount = 0;
        }
        if (InterlockedIncrement(&g_logCount) > LOG_BURST) return;
    }
    initDataDir();
    char buf[512];
    SYSTEMTIME t;
    GetSystemTime(&t);
    int k = snprintf(buf, sizeof buf, "%04d-%02d-%02d %02d:%02d:%02d pid %lu: ",
                     t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond,
                     GetCurrentProcessId());
    k += vsnprintf(buf + k, sizeof buf - k - 3, fmt, ap);
    if (k < 0) k = 0;
    if (k > (int)sizeof buf - 3) k = sizeof buf - 3;
    buf[k++] = '\r';
    buf[k++] = '\n';
    wchar_t path[MAX_PATH];
    _snwprintf(path, MAX_PATH, L"%ls\\apo.log", g_dataDir);
    path[MAX_PATH - 1] = 0;
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    LARGE_INTEGER sz;
    if (h != INVALID_HANDLE_VALUE && GetFileSizeEx(h, &sz) && sz.QuadPart > (256 << 10)) {
        CloseHandle(h);
        h = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    }
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(h, buf, k, &w, NULL);
        CloseHandle(h);
    }
}

static void logf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    logv(false, fmt, ap);
    va_end(ap);
}

static void logAlways(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    logv(true, fmt, ap);
    va_end(ap);
}

// ------------------------------------------------------------------ Sample formats
enum SampleKind { KIND_NONE = 0, KIND_F32, KIND_I16, KIND_I24, KIND_I32 };

static bool isFloat32(const WAVEFORMATEX *f) {
    if (!f || f->wBitsPerSample != 32) return false;
    if (f->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) return true;
    if (f->wFormatTag == WAVE_FORMAT_EXTENSIBLE && f->cbSize >= 22) {
        const WAVEFORMATEXTENSIBLE *x = (const WAVEFORMATEXTENSIBLE *)f;
        return IsEqualGUID(x->SubFormat, SUBTYPE_IEEE_FLOAT);
    }
    return false;
}

static SampleKind kindOf(const WAVEFORMATEX *f) {
    if (isFloat32(f)) return KIND_F32;
    if (!f || f->nChannels == 0) return KIND_NONE;
    bool pcm = f->wFormatTag == WAVE_FORMAT_PCM;
    if (f->wFormatTag == WAVE_FORMAT_EXTENSIBLE && f->cbSize >= 22) {
        const WAVEFORMATEXTENSIBLE *x = (const WAVEFORMATEXTENSIBLE *)f;
        pcm = IsEqualGUID(x->SubFormat, SUBTYPE_PCM_) && x->Samples.wValidBitsPerSample
              && x->Samples.wValidBitsPerSample <= f->wBitsPerSample;
    }
    if (!pcm || f->nBlockAlign != f->nChannels * (f->wBitsPerSample / 8)) return KIND_NONE;
    return f->wBitsPerSample == 16 ? KIND_I16 : f->wBitsPerSample == 24 ? KIND_I24
           : f->wBitsPerSample == 32 ? KIND_I32 : KIND_NONE;
}

static void intToFloat(SampleKind k, const void *src, float *dst, size_t n) {
    if (k == KIND_I16) {
        const int16_t *s = (const int16_t *)src;
        for (size_t i = 0; i < n; i++) dst[i] = (float)s[i] * (1.0f / 32768.0f);
    } else if (k == KIND_I24) {
        const uint8_t *s = (const uint8_t *)src;
        for (size_t i = 0; i < n; i++, s += 3) {
            int32_t v = (int32_t)((uint32_t)s[0] << 8 | (uint32_t)s[1] << 16 | (uint32_t)s[2] << 24);
            dst[i] = (float)(v >> 8) * (1.0f / 8388608.0f);
        }
    } else {
        const int32_t *s = (const int32_t *)src;
        for (size_t i = 0; i < n; i++) dst[i] = (float)((double)s[i] * (1.0 / 2147483648.0));
    }
}

static inline int32_t toInt(float x, double full) {
    double v = (double)x * full;
    v = v >= 0 ? v + 0.5 : v - 0.5;
    if (!(v == v)) return 0;
    if (v >= full - 1) return (int32_t)(full - 1);
    if (v <= -full) return (int32_t)-full;
    return (int32_t)v;
}

static void floatToInt(SampleKind k, const float *src, const float *before, void *dst, size_t n) {
    if (k == KIND_I16) {
        int16_t *d = (int16_t *)dst;
        for (size_t i = 0; i < n; i++)
            if (!before || src[i] != before[i]) d[i] = (int16_t)toInt(src[i], 32768.0);
    } else if (k == KIND_I24) {
        uint8_t *d = (uint8_t *)dst;
        for (size_t i = 0; i < n; i++) {
            if (before && src[i] == before[i]) continue;
            uint32_t v = (uint32_t)toInt(src[i], 8388608.0);
            d[3 * i] = (uint8_t)v;
            d[3 * i + 1] = (uint8_t)(v >> 8);
            d[3 * i + 2] = (uint8_t)(v >> 16);
        }
    } else {
        int32_t *d = (int32_t *)dst;
        for (size_t i = 0; i < n; i++)
            if (!before || src[i] != before[i]) d[i] = toInt(src[i], 2147483648.0);
    }
}

static inline float clean(float x) {
    if (!(x == x)) return 0.0f;
    return x > 1.0f ? 1.0f : x < -1.0f ? -1.0f : x;
}

// ------------------------------------------------------------------ SEH fault guard
static volatile LONG g_faults;
static volatile DWORD g_faultCode;

extern "C" __attribute__((used)) LONG clipmicGuardFilter(EXCEPTION_POINTERS *ep, void *) {
    DWORD code = ep && ep->ExceptionRecord ? ep->ExceptionRecord->ExceptionCode : 0;
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION:
    case EXCEPTION_IN_PAGE_ERROR:
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
    case EXCEPTION_DATATYPE_MISALIGNMENT:
    case EXCEPTION_INT_DIVIDE_BY_ZERO:
    case EXCEPTION_FLT_INVALID_OPERATION:
    case EXCEPTION_FLT_DIVIDE_BY_ZERO:
    case EXCEPTION_FLT_OVERFLOW:
        g_faultCode = code;
        InterlockedIncrement(&g_faults);
        return EXCEPTION_EXECUTE_HANDLER;
    default:
        return EXCEPTION_CONTINUE_SEARCH;
    }
}

#if defined(__x86_64__) && defined(__SEH__)
__attribute__((noinline)) static bool guarded(void (*fn)(void *), void *ctx) {
    volatile int ok = 0;
    __asm__ __volatile__(".l_cmguard_start:\n"
                         "\t.seh_handler __C_specific_handler, @except\n"
                         "\t.seh_handlerdata\n"
                         "\t.long 1\n"
                         "\t.rva .l_cmguard_start, .l_cmguard_end, clipmicGuardFilter, .l_cmguard_end\n"
                         "\t.text\n" ::: "memory");
    fn(ctx);
    ok = 1;
    __asm__ __volatile__("\tnop\n.l_cmguard_end:\n\tnop\n" ::: "memory");
    return ok;
}
#else
#error "clipmic_apo needs x86-64 SEH (MinGW-w64 x86_64 g++)"
#endif

static double besselI0(double x) {
    double sum = 1.0, term = 1.0;
    for (int k = 1; k < 64; k++) {
        term *= (x / (2.0 * k)) * (x / (2.0 * k));
        sum += term;
        if (term < sum * 1e-12) break;
    }
    return sum;
}

// ------------------------------------------------------------------ ClipMicAPO class
class ClipMicAPO : public IAudioProcessingObject,
                   public IAudioProcessingObjectRT,
                   public IAudioProcessingObjectConfiguration,
                   public IAudioSystemEffects {
public:
    struct Inner : public IUnknown {
        ClipMicAPO *self;
        HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
            return self->innerQuery(riid, ppv);
        }
        ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&self->m_ref); }
        ULONG STDMETHODCALLTYPE Release() override {
            LONG r = InterlockedDecrement(&self->m_ref);
            if (r == 0) delete self;
            return r;
        }
    };

    explicit ClipMicAPO(IUnknown *outer) {
        InterlockedIncrement(&g_objects);
        m_inner.self = this;
        m_outer = outer ? outer : &m_inner;
        m_id = (LONG)((GetCurrentProcessId() << 12) ^ (DWORD)InterlockedIncrement(&g_ids));
        if (!m_id) m_id = 1;
    }
    IUnknown *inner() { return &m_inner; }

    HRESULT innerQuery(REFIID riid, void **ppv) {
        if (!ppv) return E_POINTER;
        if (IsEqualIID(riid, IID_IUnknown))
            *ppv = &m_inner;
        else if (IsEqualIID(riid, IID_IAudioProcessingObject_))
            *ppv = static_cast<IAudioProcessingObject *>(this);
        else if (IsEqualIID(riid, IID_IAudioProcessingObjectRT_))
            *ppv = static_cast<IAudioProcessingObjectRT *>(this);
        else if (IsEqualIID(riid, IID_IAudioProcessingObjectConfiguration_))
            *ppv = static_cast<IAudioProcessingObjectConfiguration *>(this);
        else if (IsEqualIID(riid, IID_IAudioSystemEffects_))
            *ppv = static_cast<IAudioSystemEffects *>(this);
        else {
            *ppv = NULL;
            return E_NOINTERFACE;
        }
        static_cast<IUnknown *>(*ppv)->AddRef();
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        return m_outer->QueryInterface(riid, ppv);
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return m_outer->AddRef(); }
    ULONG STDMETHODCALLTYPE Release() override { return m_outer->Release(); }

    HRESULT STDMETHODCALLTYPE Reset() override {
        m_synced = false;
        m_played = false;
        return m_child ? m_child->Reset() : S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetLatency(HNSTIME *pTime) override {
        if (!pTime) return E_POINTER;
        if (m_child) return m_child->GetLatency(pTime);
        *pTime = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetRegistrationProperties(APO_REG_PROPERTIES **pp) override {
        if (!pp) return E_POINTER;
        APO_REG_PROPERTIES *p = (APO_REG_PROPERTIES *)CoTaskMemAlloc(sizeof(APO_REG_PROPERTIES));
        if (!p) return E_OUTOFMEMORY;
        memset(p, 0, sizeof *p);
        p->clsid = CLSID_ClipMicAPO;
        p->Flags = APO_FLAG_DEFAULT;
        wcscpy(p->szFriendlyName, L"ClipMic Direct Mic APO");
        wcscpy(p->szCopyrightInfo, L"ClipMic contributors (GPLv3)");
        p->u32MajorVersion = 1;
        p->u32MinInputConnections = p->u32MaxInputConnections = 1;
        p->u32MinOutputConnections = p->u32MaxOutputConnections = 1;
        p->u32MaxInstances = 0xffffffff;
        p->u32NumAPOInterfaces = 1;
        p->iidAPOInterfaceList[0] = IID_IAudioProcessingObject_;
        *pp = p;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Initialize(UINT32 cb, BYTE *data) override {
        bool discovery = false;
        wchar_t endpoint[64] = L"";
        if (data && cb >= sizeof(APOInitSystemEffects)) {
            APOInitSystemEffects *init = (APOInitSystemEffects *)data;
            if (cb >= sizeof(APOInitSystemEffects2))
                discovery = ((APOInitSystemEffects2 *)data)->InitializeForDiscoveryOnly != FALSE;
            if (init->pAPOEndpointProperties) {
                PROPVARIANT v;
                PropVariantInit(&v);
                if (SUCCEEDED(init->pAPOEndpointProperties->GetValue(PKEY_AudioEndpoint_GUID_, &v))
                        && v.vt == VT_LPWSTR && v.pwszVal) {
                    wcsncpy(endpoint, v.pwszVal, 63);
                    endpoint[63] = 0;
                }
                PropVariantClear(&v);
            }
            loadChild(endpoint, cb, data);
        }
        logf("initialize: endpoint %ls, discovery %d, child %s", endpoint, (int)discovery,
             m_child ? "yes" : "no");
        if (!discovery && !m_ring && !guarded([](void *p) { ((ClipMicAPO *)p)->openRing(); }, this)) {
            m_ring = NULL;
        }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE IsInputFormatSupported(IAudioMediaType *opp, IAudioMediaType *req,
                                                     IAudioMediaType **out) override {
        if (m_child) return m_child->IsInputFormatSupported(opp, req, out);
        return sameFormat(opp, req, out);
    }
    HRESULT STDMETHODCALLTYPE IsOutputFormatSupported(IAudioMediaType *opp, IAudioMediaType *req,
                                                      IAudioMediaType **out) override {
        if (m_child) return m_child->IsOutputFormatSupported(opp, req, out);
        return sameFormat(opp, req, out);
    }
    HRESULT STDMETHODCALLTYPE GetInputChannelCount(UINT32 *n) override {
        if (!n) return E_POINTER;
        if (m_child) return m_child->GetInputChannelCount(n);
        *n = m_channels;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE LockForProcess(UINT32 nIn, APO_CONNECTION_DESCRIPTOR **in,
                                             UINT32 nOut, APO_CONNECTION_DESCRIPTOR **out) override {
        if (nIn < 1 || nOut < 1 || !in || !out || !in[0] || !out[0]) return E_INVALIDARG;
        if (m_childConfig) {
            HRESULT hr = m_childConfig->LockForProcess(nIn, in, nOut, out);
            if (FAILED(hr)) {
                if (!sameShape(in[0], out[0])) return hr;
                dropChild();
            }
        }
        const WAVEFORMATEX *f = out[0]->pFormat ? out[0]->pFormat->GetAudioFormat() : NULL;
        m_kind = kindOf(f);
        m_frameBytes = f ? f->nBlockAlign : 0;
        m_channels = f ? f->nChannels : 0;
        m_rate = f ? f->nSamplesPerSec : 0;
        m_synced = false;
        m_played = false;
        m_locked = true;
        m_addW = 0.0f;
        m_repW = 0.0f;
        m_restUntil = 0;
        if (!m_ring) {
            guarded([](void *p) { ((ClipMicAPO *)p)->openRing(); }, this);
        }
        if (m_snap) HeapFree(GetProcessHeap(), 0, m_snap);
        if (m_work) HeapFree(GetProcessHeap(), 0, m_work);
        m_snap = NULL;
        m_work = NULL;
        m_snapFrames = 0;
        if (m_ring && m_rate && m_channels && m_channels <= 64) {
            UINT32 maxFrames = out[0]->u32MaxFrameCount;
            size_t bytes = (size_t)maxFrames * m_channels * sizeof(float);
            m_snap = (float *)HeapAlloc(GetProcessHeap(), 0, bytes);
            if (m_snap && m_kind != KIND_F32 && m_kind != KIND_NONE) {
                m_work = (float *)HeapAlloc(GetProcessHeap(), 0, bytes);
                if (!m_work) {
                    HeapFree(GetProcessHeap(), 0, m_snap);
                    m_snap = NULL;
                }
            }
            if (m_snap) m_snapFrames = maxFrames;
        }
        if (m_ring && m_rate) {
            makeKernel();
            m_lead = LEAD_DEFAULT_S * m_boardRate;
            guarded([](void *p) { ((ClipMicAPO *)p)->claimSlot(); }, this);
        }
        logAlways("lock: %u Hz, %u ch, kind %d, max %u frames, ring %s",
                  m_rate, m_channels, (int)m_kind, out[0]->u32MaxFrameCount, m_ring ? "open" : "missing");
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE UnlockForProcess() override {
        m_locked = false;
        releaseSlotGuarded();
        return m_childConfig ? m_childConfig->UnlockForProcess() : S_OK;
    }

    void STDMETHODCALLTYPE APOProcess(UINT32 nIn, APO_CONNECTION_PROPERTY **in, UINT32 nOut,
                                      APO_CONNECTION_PROPERTY **out) override {
        if (m_childRT) {
            m_childRT->APOProcess(nIn, in, nOut, out);
        } else if (nIn >= 1 && nOut >= 1) {
            APO_CONNECTION_PROPERTY *i = in[0], *o = out[0];
            if (o->pBuffer != i->pBuffer && i->u32BufferFlags == BUFFER_VALID)
                memcpy((void *)o->pBuffer, (const void *)i->pBuffer,
                       (size_t)i->u32ValidFrameCount * m_frameBytes);
            o->u32ValidFrameCount = i->u32ValidFrameCount;
            o->u32BufferFlags = i->u32BufferFlags;
        }
        if (nOut >= 1 && m_ring && m_channels && m_rate) {
            if (m_kind == KIND_F32) processGuarded(out[0]);
            else if (m_kind != KIND_NONE) processInt(out[0]);
        }
    }
    UINT32 STDMETHODCALLTYPE CalcInputFrames(UINT32 n) override {
        return m_childRT ? m_childRT->CalcInputFrames(n) : n;
    }
    UINT32 STDMETHODCALLTYPE CalcOutputFrames(UINT32 n) override {
        return m_childRT ? m_childRT->CalcOutputFrames(n) : n;
    }

private:
    virtual ~ClipMicAPO() {
        releaseSlotGuarded();
        dropChild();
        if (m_kernel) HeapFree(GetProcessHeap(), 0, m_kernel);
        if (m_snap) HeapFree(GetProcessHeap(), 0, m_snap);
        if (m_work) HeapFree(GetProcessHeap(), 0, m_work);
        if (m_view) UnmapViewOfFile(m_view);
        if (m_map) CloseHandle(m_map);
        if (m_file != INVALID_HANDLE_VALUE) CloseHandle(m_file);
        InterlockedDecrement(&g_objects);
    }

    HRESULT sameFormat(IAudioMediaType *opp, IAudioMediaType *req, IAudioMediaType **out) {
        if (!req || !out) return E_POINTER;
        *out = NULL;
        const WAVEFORMATEX *rf = req->GetAudioFormat();
        SampleKind k = kindOf(rf);
        if (k == KIND_NONE) return APOERR_FORMAT_NOT_SUPPORTED;
        if (opp) {
            const WAVEFORMATEX *a = opp->GetAudioFormat();
            if (a && (a->nSamplesPerSec != rf->nSamplesPerSec || a->nChannels != rf->nChannels
                      || kindOf(a) != k)) {
                opp->AddRef();
                *out = opp;
                return S_FALSE;
            }
        }
        req->AddRef();
        *out = req;
        return S_OK;
    }

    static bool sameShape(const APO_CONNECTION_DESCRIPTOR *in, const APO_CONNECTION_DESCRIPTOR *out) {
        const WAVEFORMATEX *a = in->pFormat ? in->pFormat->GetAudioFormat() : NULL;
        const WAVEFORMATEX *b = out->pFormat ? out->pFormat->GetAudioFormat() : NULL;
        SampleKind k = kindOf(a);
        return k != KIND_NONE && kindOf(b) == k && a->nChannels == b->nChannels
               && a->nSamplesPerSec == b->nSamplesPerSec;
    }

    void dropChild() {
        if (m_childConfig) m_childConfig->Release();
        if (m_childRT) m_childRT->Release();
        if (m_child) m_child->Release();
        m_childConfig = NULL;
        m_childRT = NULL;
        m_child = NULL;
    }

    static bool eapoRunsUs(const wchar_t *endpoint) {
        wchar_t key[256], ours[64];
        _snwprintf(key, 256, L"%ls\\%ls", EAPO_KEY, endpoint);
        key[255] = 0;
        if (!StringFromGUID2(CLSID_ClipMicAPO, ours, 64)) return false;
        HKEY h;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, key, 0, KEY_READ, &h) != ERROR_SUCCESS) return false;
        bool found = false;
        for (DWORD i = 0; !found; i++) {
            wchar_t name[256], value[64];
            DWORD nlen = 256, vlen = sizeof value, type = 0;
            LONG r = RegEnumValueW(h, i, name, &nlen, NULL, &type, (BYTE *)value, &vlen);
            if (r == ERROR_NO_MORE_ITEMS) break;
            if (r != ERROR_SUCCESS || type != REG_SZ) continue;
            value[63] = 0;
            found = _wcsicmp(value, ours) == 0;
        }
        RegCloseKey(h);
        return found;
    }

    static HRESULT createDriverApo(const wchar_t *clsidText, REFCLSID clsid,
                                   IAudioProcessingObject **out) {
        HKEY cls;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, APO_CLASS_KEY, 0, KEY_READ, &cls) != ERROR_SUCCESS)
            return REGDB_E_CLASSNOTREG;
        HRESULT hr = REGDB_E_CLASSNOTREG;
        for (DWORD i = 0; hr == REGDB_E_CLASSNOTREG; i++) {
            wchar_t sub[64], key[192], raw[MAX_PATH], path[MAX_PATH];
            DWORD n = 64;
            LONG r = RegEnumKeyExW(cls, i, sub, &n, NULL, NULL, NULL, NULL);
            if (r == ERROR_NO_MORE_ITEMS) break;
            if (r != ERROR_SUCCESS) continue;
            _snwprintf(key, 192, L"%ls\\Classes\\CLSID\\%ls\\InprocServer32", sub, clsidText);
            key[191] = 0;
            DWORD size = sizeof raw;
            if (RegGetValueW(cls, key, NULL, RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND,
                             NULL, raw, &size) != ERROR_SUCCESS)
                continue;
            DWORD len = ExpandEnvironmentStringsW(raw, path, MAX_PATH);
            if (!len || len > MAX_PATH || path[0] == 0 || path[1] != L':') break;
            HMODULE dll = LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
            if (!dll) {
                hr = HRESULT_FROM_WIN32(GetLastError());
                break;
            }
            typedef HRESULT(STDAPICALLTYPE * GetClassObjectFn)(REFCLSID, REFIID, void **);
            GetClassObjectFn get = (GetClassObjectFn)(void *)GetProcAddress(dll, "DllGetClassObject");
            IClassFactory *cf = NULL;
            hr = get ? get(clsid, IID_IClassFactory, (void **)&cf) : CLASS_E_CLASSNOTAVAILABLE;
            if (SUCCEEDED(hr) && cf) {
                hr = cf->CreateInstance(NULL, IID_IAudioProcessingObject_, (void **)out);
                cf->Release();
            } else if (SUCCEEDED(hr)) {
                hr = E_NOINTERFACE;
            }
            if (FAILED(hr) || !*out) {
                *out = NULL;
                FreeLibrary(dll);
                if (SUCCEEDED(hr)) hr = E_NOINTERFACE;
            }
        }
        RegCloseKey(cls);
        return hr;
    }

    void loadChild(const wchar_t *endpoint, UINT32 cb, BYTE *data) {
        if (!endpoint[0] || m_child || t_loading) return;
        struct Loading {
            Loading() { t_loading++; }
            ~Loading() { t_loading--; }
        } loading;
        wchar_t key[256], value[64];
        _snwprintf(key, 256, L"%ls\\%ls", STATE_KEY, endpoint);
        key[255] = 0;
        DWORD size = sizeof value;
        if (RegGetValueW(CLIPMIC_STATE_ROOT, key, L"Original", RRF_RT_REG_SZ, NULL, value, &size)
                != ERROR_SUCCESS || !value[0])
            return;
        CLSID clsid;
        if (FAILED(CLSIDFromString(value, &clsid)) || IsEqualCLSID(clsid, CLSID_ClipMicAPO)) return;
        if ((IsEqualCLSID(clsid, CLSID_EapoPreMix) || IsEqualCLSID(clsid, CLSID_EapoPostMix))
                && eapoRunsUs(endpoint))
            return;
        IAudioProcessingObject *child = NULL;
        HRESULT hr = CoCreateInstance(clsid, NULL, CLSCTX_INPROC_SERVER, IID_IAudioProcessingObject_,
                                      (void **)&child);
        if (hr == REGDB_E_CLASSNOTREG) hr = createDriverApo(value, clsid, &child);
        if (FAILED(hr) || !child) return;
        BYTE *copy = (BYTE *)CoTaskMemAlloc(cb);
        if (!copy) { child->Release(); return; }
        memcpy(copy, data, cb);
        ((APOInitBaseStruct *)copy)->clsid = clsid;
        hr = child->Initialize(cb, copy);
        CoTaskMemFree(copy);
        if (FAILED(hr)) { child->Release(); return; }
        if (FAILED(child->QueryInterface(IID_IAudioProcessingObjectRT_, (void **)&m_childRT)) ||
            FAILED(child->QueryInterface(IID_IAudioProcessingObjectConfiguration_,
                                         (void **)&m_childConfig))) {
            if (m_childRT) m_childRT->Release();
            m_childRT = NULL;
            m_childConfig = NULL;
            child->Release();
            return;
        }
        m_child = child;
    }

    void makeKernel() {
        if (m_kernel) HeapFree(GetProcessHeap(), 0, m_kernel);
        m_kernel = NULL;
        m_half = 0;
        if (m_rate == m_boardRate) return;
        double r = m_rate < m_boardRate ? (double)m_rate / m_boardRate : 1.0;
        double fc = 0.5 * r * (1.0 + SINC_PASS) / 2.0;
        double df = 0.5 * r * (1.0 - SINC_PASS);
        double beta = 0.1102 * (SINC_ATTEN_DB - 8.7);
        int taps = (int)ceil((SINC_ATTEN_DB - 8.0) / (2.285 * 2.0 * PI_ * df)) + 1;
        int half = (taps + 1) / 2;
        if (half < 4) half = 4;
        if (half > 512) half = 512;
        size_t n = (size_t)(PHASES + 1) * 2 * half;
        float *k = (float *)HeapAlloc(GetProcessHeap(), 0, n * sizeof(float));
        if (!k) return;
        double i0b = besselI0(beta);
        for (int p = 0; p <= PHASES; p++) {
            float *row = k + (size_t)p * 2 * half;
            double sum = 0.0;
            for (int j = 0; j < 2 * half; j++) {
                double t = (double)(j - half + 1) - (double)p / PHASES;
                double u = t / half;
                double w = u * u < 1.0 ? besselI0(beta * sqrt(1.0 - u * u)) / i0b : 0.0;
                double x = 2.0 * fc * t;
                double sinc = fabs(x) < 1e-9 ? 1.0 : sin(PI_ * x) / (PI_ * x);
                row[j] = (float)(2.0 * fc * sinc * w);
                sum += row[j];
            }
            for (int j = 0; j < 2 * half; j++) row[j] = (float)(row[j] / sum);
        }
        m_kernel = k;
        m_half = half;
    }

    inline float sample(double pos) const {
        if (m_kernel) {
            double x = pos - m_half;
            double fk = floor(x);
            uint64_t k = (uint64_t)(int64_t)fk;
            float pf = (float)(x - fk) * PHASES;
            int pi = (int)pf;
            if (pi >= PHASES) pi = PHASES - 1;
            if (pi < 0) pi = 0;
            float fr = pf - (float)pi;
            const float *a = m_kernel + (size_t)pi * 2 * m_half, *b = a + 2 * m_half;
            uint64_t first = k - (uint64_t)m_half + 1;
            float acc = 0.0f;
            for (int j = 0; j < 2 * m_half; j++)
                acc += m_data[(first + j) & m_mask] * (a[j] + (b[j] - a[j]) * fr);
            return clean(acc);
        }
        uint64_t k = (uint64_t)pos;
        float f = (float)(pos - (double)k);
        float a = clean(m_data[k & m_mask]), b = clean(m_data[(k + 1) & m_mask]);
        return a + (b - a) * f;
    }

    void openRing() {
        if (m_ring) return;
        initDataDir();
        wchar_t path[MAX_PATH];
        _snwprintf(path, MAX_PATH, L"%ls\\ring.bin", g_dataDir);
        path[MAX_PATH - 1] = 0;
        m_file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                             OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (m_file == INVALID_HANDLE_VALUE) {
            logAlways("openRing failed on %ls: error %lu", path, (unsigned long)GetLastError());
            return;
        }
        LARGE_INTEGER size;
        if (!GetFileSizeEx(m_file, &size) || size.QuadPart < HEADER_BYTES) {
            logAlways("openRing: ring file too small");
            return;
        }
        m_map = CreateFileMappingW(m_file, NULL, PAGE_READWRITE, 0, 0, NULL);
        if (!m_map) {
            logAlways("openRing: CreateFileMapping failed: error %lu", (unsigned long)GetLastError());
            return;
        }
        RingHeader *h = (RingHeader *)MapViewOfFile(m_map, FILE_MAP_ALL_ACCESS, 0, 0, 0);
        if (!h) {
            logAlways("openRing: MapViewOfFile failed: error %lu", (unsigned long)GetLastError());
            return;
        }
        m_view = h;
        MEMORY_BASIC_INFORMATION mbi;
        uint64_t mapped = 0;
        if (VirtualQuery(h, &mbi, sizeof mbi) == sizeof mbi && mbi.BaseAddress == (void *)h)
            mapped = mbi.RegionSize;
        if ((uint64_t)size.QuadPart < mapped) mapped = (uint64_t)size.QuadPart;
        if (mapped < HEADER_BYTES) return;
        const volatile RingHeader *vh = h;
        uint32_t magic = vh->magic, version = vh->version;
        uint32_t cap = vh->capacity, rate = vh->rate;
        uint64_t need = HEADER_BYTES + (uint64_t)cap * sizeof(float);
        if (magic != RING_MAGIC || version != RING_VERSION || !cap || (cap & (cap - 1)) ||
                cap > (1u << 22) || mapped < need || rate < 8000 || rate > 384000)
            return;
        m_ring = h;
        m_data = (const float *)((const BYTE *)h + HEADER_BYTES);
        m_mask = cap - 1;
        m_boardRate = rate;
        h->effect_version = EFFECT_VERSION;
    }

    Slot *slots() { return (Slot *)((BYTE *)m_ring + SLOT_OFFSET); }

    void claimSlot() {
        if (m_slot || !m_ring) return;
        uint64_t now = GetTickCount64();
        Slot *s = slots();
        for (int pass = 0; pass < 2 && !m_slot; pass++) {
            for (uint32_t i = 0; i < SLOTS; i++) {
                LONG cur = s[i].owner;
                bool free = cur == 0 || (pass == 1 && now - s[i].tick > SLOT_STALE_MS);
                if (!free) continue;
                if (InterlockedCompareExchange(&s[i].owner, m_id, cur) == cur) {
                    m_slot = &s[i];
                    break;
                }
            }
        }
        if (!m_slot) return;
        m_slot->tick = now;
        m_slot->rate = m_rate;
        m_slot->channels = m_channels;
        m_slot->flags = 0;
        m_slot->underruns = 0;
        m_slot->blocks = 0;
        m_slot->lead = (uint32_t)m_lead;
    }

    void releaseSlot() {
        if (!m_slot) return;
        InterlockedCompareExchange(&m_slot->owner, 0, m_id);
        m_slot = NULL;
    }

    void releaseSlotGuarded() {
        if (m_slot && !guarded([](void *p) { ((ClipMicAPO *)p)->releaseSlot(); }, this))
            m_slot = NULL;
    }

    bool headerOk() const {
        const volatile RingHeader *h = m_ring;
        return h->magic == RING_MAGIC && h->version == RING_VERSION &&
               h->capacity == m_mask + 1 && h->rate == m_boardRate;
    }

    struct ProcessCall { ClipMicAPO *self; APO_CONNECTION_PROPERTY *o; };

    bool processGuarded(APO_CONNECTION_PROPERTY *o) {
        uint64_t now = GetTickCount64();
        if (m_restUntil && now < m_restUntil) return false;
        m_restUntil = 0;
        UINT32 frames = o->u32ValidFrameCount;
        if (!m_snap || frames > m_snapFrames || !o->pBuffer) return false;
        APO_BUFFER_FLAGS flags = o->u32BufferFlags;
        size_t bytes = (size_t)frames * m_channels * sizeof(float);
        memcpy(m_snap, (const void *)o->pBuffer, bytes);
        ProcessCall call = {this, o};
        if (guarded([](void *p) {
                ProcessCall *c = (ProcessCall *)p;
                c->self->process(c->o);
            }, &call))
            return true;
        memcpy((void *)o->pBuffer, m_snap, bytes);
        o->u32ValidFrameCount = frames;
        o->u32BufferFlags = flags;
        m_synced = false;
        m_addW = 0.0f;
        m_repW = 0.0f;
        m_restUntil = now + FAULT_REST_MS;
        return false;
    }

    void processInt(APO_CONNECTION_PROPERTY *o) {
        UINT32 frames = o->u32ValidFrameCount;
        if (!m_work || frames > m_snapFrames || !o->pBuffer) return;
        size_t n = (size_t)frames * m_channels;
        bool valid = o->u32BufferFlags == BUFFER_VALID;
        if (valid) intToFloat(m_kind, (const void *)o->pBuffer, m_work, n);
        else memset(m_work, 0, n * sizeof(float));
        APO_CONNECTION_PROPERTY w = *o;
        w.pBuffer = (UINT_PTR)m_work;
        if (!processGuarded(&w)) return;
        o->u32BufferFlags = w.u32BufferFlags;
        if (w.u32BufferFlags != BUFFER_VALID) return;
        floatToInt(m_kind, m_work, valid ? m_snap : NULL, (void *)o->pBuffer, n);
    }

    void process(APO_CONNECTION_PROPERTY *o) {
        RingHeader *h = m_ring;
        if (!headerOk()) {
            m_synced = false;
            m_addW = 0.0f;
            m_repW = 0.0f;
            return;
        }
        uint64_t now = GetTickCount64();
        if (m_slot && m_slot->owner != m_id) m_slot = NULL;
        if (!m_slot && m_locked) claimSlot();
        UINT32 frames = o->u32ValidFrameCount;
        UINT32 ch = m_channels;
        float *buf = (float *)o->pBuffer;
        bool silent = o->u32BufferFlags != BUFFER_VALID;
        uint64_t wp = h->write_pos;
        bool board = h->enabled && now - h->board_tick <= BOARD_STALE_MS && wp <= POS_MAX;
        bool replaceMode = h->mode == MODE_REPLACE;
        double step = (double)m_boardRate / (double)m_rate;
        double cap = (double)(m_mask + 1);

        if (board && !m_synced && (double)wp < m_lead + frames * step + 2.0) board = false;
        if (board && !m_synced && m_played && (double)wp - m_lead < m_pos && m_pos <= (double)wp)
            board = false;

        if (board && (!m_synced || m_pos > (double)wp ||
                      (double)wp - m_pos > m_lead + 0.15 * m_boardRate ||
                      (double)wp - m_pos > cap * 0.5)) {
            m_pos = (double)wp - m_lead;
            if (m_pos < 0) m_pos = 0;
            m_synced = true;
        }

        double avail = m_synced ? (double)wp - m_pos : -1.0;
        double need = (frames - 1) * step + 1.0;
        bool have = avail >= need && avail <= cap * 0.5;

        float g = h->gain;
        if (!(g >= 0.0f && g < 16.0f)) g = 1.0f;
        float mg = h->mic_gain;
        if (!(mg >= 0.0f && mg <= 4.0f)) mg = 1.0f;
        if (!board) mg = 1.0f;

        float ramp = 1.0f / (float)(FADE_S * m_rate);
        float addTarget = (board && have && !replaceMode) ? 1.0f : 0.0f;
        float repTarget = (board && have && replaceMode) ? 1.0f : 0.0f;
        float addW = m_addW, repW = m_repW;
        bool any = false;

        if (have || addW > 0.0f || repW > 0.0f || mg != 1.0f) {
            if (silent) memset(buf, 0, (size_t)frames * ch * sizeof(float));
            double pos = m_pos;
            for (UINT32 i = 0; i < frames; i++) {
                float s = have ? sample(pos) * g : 0.0f;
                if (have) pos += step;
                addW += addW < addTarget ? ramp : addW > addTarget ? -ramp : 0.0f;
                if (addW < 0.0f) addW = 0.0f; else if (addW > 1.0f) addW = 1.0f;
                repW += repW < repTarget ? ramp : repW > repTarget ? -ramp : 0.0f;
                if (repW < 0.0f) repW = 0.0f; else if (repW > 1.0f) repW = 1.0f;
                float keep = (1.0f - repW) * mg;
                float inj = s * (replaceMode ? repW : addW);
                float *p = buf + (size_t)i * ch;
                for (UINT32 c = 0; c < ch; c++) {
                    float x = p[c] * keep + inj;
                    p[c] = x > 1.0f ? 1.0f : x < -1.0f ? -1.0f : x;
                }
                any = any || inj != 0.0f || keep != 1.0f;
            }
            if (have) {
                m_pos = pos;
                m_played = true;
            }
        }
        m_addW = addW;
        m_repW = repW;
        if (!have) m_synced = false;
        if (any) o->u32BufferFlags = BUFFER_VALID;
        if (m_slot && m_slot->owner == m_id) {
            m_slot->rate = m_rate;
            m_slot->channels = ch;
            m_slot->read_pos = (uint64_t)m_pos;
            m_slot->blocks = m_slot->blocks + 1;
            m_slot->tick = now;
        }
    }

    LONG m_ref = 1;
    Inner m_inner;
    IUnknown *m_outer = NULL;
    LONG m_id = 0;
    IAudioProcessingObject *m_child = NULL;
    IAudioProcessingObjectRT *m_childRT = NULL;
    IAudioProcessingObjectConfiguration *m_childConfig = NULL;
    HANDLE m_file = INVALID_HANDLE_VALUE;
    HANDLE m_map = NULL;
    void *m_view = NULL;
    RingHeader *m_ring = NULL;
    Slot *m_slot = NULL;
    float *m_snap = NULL;
    float *m_work = NULL;
    UINT32 m_snapFrames = 0;
    uint64_t m_restUntil = 0;
    const float *m_data = NULL;
    uint32_t m_mask = 0;
    uint32_t m_boardRate = 48000;
    UINT32 m_channels = 0;
    UINT32 m_rate = 0;
    SampleKind m_kind = KIND_NONE;
    UINT32 m_frameBytes = 0;
    bool m_locked = false;
    bool m_synced = false;
    bool m_played = false;
    double m_pos = 0;
    double m_lead = 0;
    float *m_kernel = NULL;
    int m_half = 0;
    float m_addW = 0.0f;
    float m_repW = 0.0f;
};

// ------------------------------------------------------------------ COM exports
class Factory : public IClassFactory {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IClassFactory)) {
            *ppv = static_cast<IClassFactory *>(this);
            AddRef();
            return S_OK;
        }
        *ppv = NULL;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&g_factoryRefs); }
    ULONG STDMETHODCALLTYPE Release() override { return InterlockedDecrement(&g_factoryRefs); }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown *outer, REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = NULL;
        if (outer && !IsEqualIID(riid, IID_IUnknown)) return CLASS_E_NOAGGREGATION;
        ClipMicAPO *apo = new (std::nothrow) ClipMicAPO(outer);
        if (!apo) return E_OUTOFMEMORY;
        HRESULT hr = apo->innerQuery(riid, ppv);
        apo->inner()->Release();
        return hr;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_locks);
        else InterlockedDecrement(&g_locks);
        return S_OK;
    }
};

static Factory g_factory;

extern "C" __declspec(dllexport) HRESULT STDAPICALLTYPE DllGetClassObject(REFCLSID clsid, REFIID riid,
                                                                          void **ppv) {
    if (!ppv) return E_POINTER;
    *ppv = NULL;
    if (!IsEqualCLSID(clsid, CLSID_ClipMicAPO)) return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, ppv);
}

extern "C" __declspec(dllexport) HRESULT STDAPICALLTYPE DllCanUnloadNow() {
    return (g_objects == 0 && g_locks == 0 && g_factoryRefs == 0) ? S_OK : S_FALSE;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = inst;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}
