// ClipMic APO offline test host (GPLv3, derived from Onion Board testhost.cpp)
// Loads clipmic_apo.dll via DllGetClassObject and runs APOProcess on synthetic mic blocks
// without touching Windows audio drivers or registry.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objbase.h>
#include <mmreg.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <math.h>
#include <string.h>

typedef enum { BUFFER_INVALID = 0, BUFFER_VALID = 1, BUFFER_SILENT = 2 } APO_BUFFER_FLAGS;
struct APO_CONNECTION_PROPERTY { UINT_PTR pBuffer; UINT32 u32ValidFrameCount;
                                 APO_BUFFER_FLAGS u32BufferFlags; UINT32 u32Signature; };
struct IAudioMediaType;
struct APO_CONNECTION_DESCRIPTOR { int Type; UINT_PTR pBuffer; UINT32 u32MaxFrameCount;
                                   IAudioMediaType *pFormat; UINT32 u32Signature; };
struct IAudioMediaType : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE IsCompressedFormat(BOOL *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsEqual(IAudioMediaType *, DWORD *) = 0;
    virtual const WAVEFORMATEX *STDMETHODCALLTYPE GetAudioFormat() = 0;
    virtual HRESULT STDMETHODCALLTYPE GetUncompressedAudioFormat(void *) = 0;
};
struct IAudioProcessingObjectRT : public IUnknown {
    virtual void STDMETHODCALLTYPE APOProcess(UINT32, APO_CONNECTION_PROPERTY **, UINT32,
                                              APO_CONNECTION_PROPERTY **) = 0;
    virtual UINT32 STDMETHODCALLTYPE CalcInputFrames(UINT32) = 0;
    virtual UINT32 STDMETHODCALLTYPE CalcOutputFrames(UINT32) = 0;
};
struct IAudioProcessingObjectConfiguration : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE LockForProcess(UINT32, APO_CONNECTION_DESCRIPTOR **, UINT32,
                                                     APO_CONNECTION_DESCRIPTOR **) = 0;
    virtual HRESULT STDMETHODCALLTYPE UnlockForProcess() = 0;
};
struct IAudioProcessingObject : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Reset() = 0;
    virtual HRESULT STDMETHODCALLTYPE GetLatency(LONGLONG *) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetRegistrationProperties(void **) = 0;
    virtual HRESULT STDMETHODCALLTYPE Initialize(UINT32, BYTE *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsInputFormatSupported(IAudioMediaType *, IAudioMediaType *,
                                                             IAudioMediaType **) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsOutputFormatSupported(IAudioMediaType *, IAudioMediaType *,
                                                              IAudioMediaType **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetInputChannelCount(UINT32 *) = 0;
};

static const IID IID_APO = {0xfd7f2b29, 0x24d0, 0x4b5c, {0xb1, 0x77, 0x59, 0x2c, 0x39, 0xf9, 0xca, 0x10}};
static const IID IID_RT  = {0x9e1d6a6d, 0xddbc, 0x4e95, {0xa4, 0xc7, 0xad, 0x64, 0xba, 0x37, 0x84, 0x6c}};
static const IID IID_CFG = {0x0e5ed805, 0xaba6, 0x49c3, {0x8f, 0x9a, 0x2b, 0x8c, 0x88, 0x9c, 0x4f, 0xa8}};
static const CLSID CLSID_ClipMicAPO = {0xa4e7b219, 0x8f3c, 0x4d6a, {0x9e, 0x12, 0x7b, 0x5c, 0x1d, 0x3f, 0x8a, 0x42}};

class MediaType : public IAudioMediaType {
public:
    WAVEFORMATEXTENSIBLE f;
    MediaType(UINT32 rate, UINT32 ch) {
        memset(&f, 0, sizeof f);
        f.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
        f.Format.nChannels = (WORD)ch;
        f.Format.nSamplesPerSec = rate;
        f.Format.wBitsPerSample = 32;
        f.Format.nBlockAlign = (WORD)(4 * ch);
        f.Format.nAvgBytesPerSec = rate * 4 * ch;
        f.Format.cbSize = 22;
        f.Samples.wValidBitsPerSample = 32;
        f.SubFormat = {0x00000003u, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **p) override { *p = NULL; return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE IsCompressedFormat(BOOL *c) override { *c = FALSE; return S_OK; }
    HRESULT STDMETHODCALLTYPE IsEqual(IAudioMediaType *, DWORD *) override { return E_NOTIMPL; }
    const WAVEFORMATEX *STDMETHODCALLTYPE GetAudioFormat() override { return &f.Format; }
    HRESULT STDMETHODCALLTYPE GetUncompressedAudioFormat(void *) override { return E_NOTIMPL; }
};

class Outer : public IUnknown {
public:
    IUnknown *inner = NULL;
    LONG ref = 1;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **p) override {
        if (IsEqualIID(riid, IID_IUnknown)) { *p = this; AddRef(); return S_OK; }
        return inner->QueryInterface(riid, p);
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&ref); }
    ULONG STDMETHODCALLTYPE Release() override { return InterlockedDecrement(&ref); }
};

typedef HRESULT(STDAPICALLTYPE *GetClassObjectFn)(REFCLSID, REFIID, void **);

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: testhost <clipmic_apo.dll> <out.f32> <rate> <channels> [blocks]\n");
        return 2;
    }
    UINT32 rate = (UINT32)atoi(argv[3]), ch = (UINT32)atoi(argv[4]);
    int blocks = argc > 5 ? atoi(argv[5]) : 20;
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { fprintf(stderr, "LoadLibrary failed\n"); return 1; }
    GetClassObjectFn get = (GetClassObjectFn)(void *)GetProcAddress(dll, "DllGetClassObject");
    IClassFactory *cf = NULL;
    if (!get || FAILED(get(CLSID_ClipMicAPO, IID_IClassFactory, (void **)&cf))) {
        fprintf(stderr, "DllGetClassObject failed\n");
        return 1;
    }
    Outer outer;
    IUnknown *inner = NULL;
    if (FAILED(cf->CreateInstance(&outer, IID_IUnknown, (void **)&inner))) {
        fprintf(stderr, "CreateInstance failed\n");
        return 1;
    }
    outer.inner = inner;
    IAudioProcessingObject *apo = NULL;
    IAudioProcessingObjectRT *rt = NULL;
    IAudioProcessingObjectConfiguration *cfg = NULL;
    outer.QueryInterface(IID_APO, (void **)&apo);
    apo->QueryInterface(IID_RT, (void **)&rt);
    apo->QueryInterface(IID_CFG, (void **)&cfg);
    if (!apo || !rt || !cfg || FAILED(apo->Initialize(0, NULL))) {
        fprintf(stderr, "Initialize failed\n");
        return 1;
    }
    UINT32 blockFrames = rate / 100;  // 10 ms
    MediaType mt(rate, ch);
    float *inBuf = (float *)calloc((size_t)blockFrames * ch, sizeof(float));
    float *outBuf = (float *)calloc((size_t)blockFrames * ch, sizeof(float));
    APO_CONNECTION_DESCRIPTOR inDesc = {1, (UINT_PTR)inBuf, blockFrames, &mt, 0};
    APO_CONNECTION_DESCRIPTOR outDesc = {1, (UINT_PTR)outBuf, blockFrames, &mt, 0};
    APO_CONNECTION_DESCRIPTOR *pIn = &inDesc, *pOut = &outDesc;
    if (FAILED(cfg->LockForProcess(1, &pIn, 1, &pOut))) {
        fprintf(stderr, "LockForProcess failed\n");
        return 1;
    }
    FILE *fp = fopen(argv[2], "wb");
    for (int b = 0; b < blocks; b++) {
        memset(inBuf, 0, (size_t)blockFrames * ch * sizeof(float));
        APO_CONNECTION_PROPERTY inProp = {(UINT_PTR)inBuf, blockFrames, BUFFER_SILENT, 0};
        APO_CONNECTION_PROPERTY outProp = {(UINT_PTR)outBuf, blockFrames, BUFFER_SILENT, 0};
        APO_CONNECTION_PROPERTY *ip = &inProp, *op = &outProp;
        rt->APOProcess(1, &ip, 1, &op);
        if (fp) fwrite(outBuf, sizeof(float), (size_t)blockFrames * ch, fp);
        Sleep(10);
    }
    if (fp) fclose(fp);
    cfg->UnlockForProcess();
    cfg->Release();
    rt->Release();
    apo->Release();
    inner->Release();
    cf->Release();
    free(inBuf);
    free(outBuf);
    FreeLibrary(dll);
    CoUninitialize();
    printf("testhost OK (%d blocks)\n", blocks);
    return 0;
}
