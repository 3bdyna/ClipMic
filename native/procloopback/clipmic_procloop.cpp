// ClipMic Per-Process Audio Loopback Helper DLL
// Uses Windows 10 (19041+/20348+) / Windows 11 ActivateAudioInterfaceAsync with
// VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK to capture a target process tree's audio
// independently of endpoint master volume, mute state, or output speaker/TV.

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>
#include <objbase.h>
#include <mmreg.h>
#include <ks.h>
#include <ksmedia.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <propidl.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <vector>

// Process loopback activation constants/structs (guarded for MinGW header compatibility)
#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
static const wchar_t kVirtualAudioDeviceProcessLoopback[] = L"VAD\\Process_Loopback";
#define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK kVirtualAudioDeviceProcessLoopback
#endif

#ifndef AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
#define AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM 0x80000000
#endif
#ifndef AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
#define AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY 0x08000000
#endif

namespace {

// Local definitions matching Windows SDK audioclientactivationparams.h
enum CM_AUDIOCLIENT_ACTIVATION_TYPE : uint32_t {
    CM_AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0,
    CM_AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1,
};

enum CM_PROCESS_LOOPBACK_MODE : uint32_t {
    CM_PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0,
    CM_PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1,
};

struct CM_AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
    DWORD TargetProcessId;
    CM_PROCESS_LOOPBACK_MODE ProcessLoopbackMode;
};

struct CM_AUDIOCLIENT_ACTIVATION_PARAMS {
    CM_AUDIOCLIENT_ACTIVATION_TYPE ActivationType;
    union {
        CM_AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams;
    };
};

// Standard Windows GUIDs (defined locally so we don't depend on extra SDK libs)
static const GUID kIID_IAudioClient =
    {0x1CB9AD4C, 0xDBFA, 0x4C32, {0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2}};
static const GUID kIID_IAudioCaptureClient =
    {0xC8ADBD64, 0xE71E, 0x48A0, {0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17}};
static const GUID kIID_IActivateAudioInterfaceCompletionHandler =
    {0x41D949AB, 0x9862, 0x444A, {0x80, 0xF6, 0xC2, 0x61, 0x33, 0x4D, 0xA5, 0xEB}};
static const GUID kIID_IAgileObject =
    {0x94EA2B94, 0xE9CC, 0x49E0, {0xC0, 0xFF, 0xEE, 0x64, 0xCA, 0x8F, 0x5B, 0x90}};
static const GUID kKSDATAFORMAT_SUBTYPE_IEEE_FLOAT =
    {0x00000003, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};

typedef HRESULT(WINAPI* PFN_ActivateAudioInterfaceAsync)(
    LPCWSTR deviceInterfacePath,
    REFIID riid,
    PROPVARIANT* activationParams,
    IActivateAudioInterfaceCompletionHandler* completionHandler,
    IActivateAudioInterfaceAsyncOperation** activationOperation);

static PFN_ActivateAudioInterfaceAsync GetActivateAudioInterfaceAsync() {
    static std::atomic<PFN_ActivateAudioInterfaceAsync> s_fn{nullptr};
    static std::atomic<bool> s_checked{false};
    if (!s_checked.load(std::memory_order_acquire)) {
        HMODULE mod = LoadLibraryW(L"mmdevapi.dll");
        PFN_ActivateAudioInterfaceAsync fn = nullptr;
        if (mod) {
            FARPROC raw = GetProcAddress(mod, "ActivateAudioInterfaceAsync");
            fn = reinterpret_cast<PFN_ActivateAudioInterfaceAsync>(
                reinterpret_cast<void*>(raw));
        }
        s_fn.store(fn, std::memory_order_relaxed);
        s_checked.store(true, std::memory_order_release);
    }
    return s_fn.load(std::memory_order_relaxed);
}

static int64_t QueryQpc() {
    LARGE_INTEGER li{};
    QueryPerformanceCounter(&li);
    return static_cast<int64_t>(li.QuadPart);
}

static int64_t QueryQpcFreq() {
    static int64_t s_freq = []() {
        LARGE_INTEGER li{};
        QueryPerformanceFrequency(&li);
        return li.QuadPart > 0 ? static_cast<int64_t>(li.QuadPart) : 10000000LL;
    }();
    return s_freq;
}

class ActivationHandler final : public IActivateAudioInterfaceCompletionHandler {
public:
    ActivationHandler()
        : ref_count_(1),
          done_event_(CreateEventW(nullptr, TRUE, FALSE, nullptr)),
          activate_hr_(E_FAIL),
          audio_client_(nullptr) {}

    ~ActivationHandler() {
        if (audio_client_) {
            audio_client_->Release();
            audio_client_ = nullptr;
        }
        if (done_event_) {
            CloseHandle(done_event_);
            done_event_ = nullptr;
        }
    }

    // IUnknown
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        if (IsEqualIID(riid, IID_IUnknown) ||
            IsEqualIID(riid, kIID_IActivateAudioInterfaceCompletionHandler) ||
            IsEqualIID(riid, kIID_IAgileObject)) {
            *ppv = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&ref_count_));
    }

    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r = static_cast<ULONG>(InterlockedDecrement(&ref_count_));
        if (r == 0) {
            delete this;
        }
        return r;
    }

    // IActivateAudioInterfaceCompletionHandler
    HRESULT STDMETHODCALLTYPE ActivateCompleted(
        IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT hr_act = E_FAIL;
        IUnknown* unk = nullptr;
        HRESULT hr = operation ? operation->GetActivateResult(&hr_act, &unk) : E_POINTER;
        if (SUCCEEDED(hr) && SUCCEEDED(hr_act) && unk) {
            IAudioClient* client = nullptr;
            hr_act = unk->QueryInterface(kIID_IAudioClient, reinterpret_cast<void**>(&client));
            if (SUCCEEDED(hr_act) && client) {
                audio_client_ = client;
            }
        } else if (FAILED(hr)) {
            hr_act = hr;
        }
        if (unk) {
            unk->Release();
        }
        activate_hr_ = hr_act;
        if (done_event_) {
            SetEvent(done_event_);
        }
        return S_OK;
    }

    HRESULT WaitAndTake(IAudioClient** out_client, DWORD timeout_ms = 3000) {
        if (!done_event_) return E_HANDLE;
        DWORD wr = WaitForSingleObject(done_event_, timeout_ms);
        if (wr != WAIT_OBJECT_0) {
            return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
        if (SUCCEEDED(activate_hr_) && audio_client_) {
            *out_client = audio_client_;
            audio_client_ = nullptr;
        }
        return activate_hr_;
    }

private:
    LONG ref_count_;
    HANDLE done_event_;
    HRESULT activate_hr_;
    IAudioClient* audio_client_;
};

static HRESULT ActivateProcessLoopbackClient(DWORD pid, IAudioClient** out_client) {
    *out_client = nullptr;
    auto fn = GetActivateAudioInterfaceAsync();
    if (!fn) {
        return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
    }

    CM_AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = CM_AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode =
        CM_PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

    PROPVARIANT pv{};
    pv.vt = VT_BLOB;
    pv.blob.cbSize = sizeof(params);
    pv.blob.pBlobData = reinterpret_cast<BYTE*>(&params);

    ActivationHandler* handler = new ActivationHandler();
    IActivateAudioInterfaceAsyncOperation* op = nullptr;
    HRESULT hr = fn(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        kIID_IAudioClient,
        &pv,
        handler,
        &op);
    if (SUCCEEDED(hr)) {
        hr = handler->WaitAndTake(out_client, 3000);
    }
    if (op) {
        op->Release();
    }
    handler->Release();
    return hr;
}

static constexpr uint32_t kSampleRate = 48000;
static constexpr uint32_t kChannels = 2;
static constexpr uint32_t kMaxDurationSeconds = 180;

static uint32_t ClampCapacityFrames(double duration_s) {
    double clamped_s = duration_s;
    if (clamped_s < 1.0) clamped_s = 1.0;
    if (clamped_s > static_cast<double>(kMaxDurationSeconds)) {
        clamped_s = static_cast<double>(kMaxDurationSeconds);
    }
    uint32_t cap = static_cast<uint32_t>(std::llround(clamped_s * static_cast<double>(kSampleRate)));
    constexpr uint32_t kMaxCap = kMaxDurationSeconds * kSampleRate;
    if (cap < kSampleRate) cap = kSampleRate;
    if (cap > kMaxCap) cap = kMaxCap;
    return cap;
}

static inline int16_t FloatToInt16(float s) {
    if (!(s == s)) return 0;  // NaN guard
    if (s >= 1.0f) return 32767;
    if (s <= -1.0f) return -32768;
    return static_cast<int16_t>(std::lrintf(s * 32767.0f));
}

static inline void ConvertFloatToInt16(int16_t* dst, const float* src, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        dst[i] = FloatToInt16(src[i]);
    }
}

struct ProcLoopSession {
    DWORD pid = 0;
    uint32_t capacity_frames = 0;
    std::vector<int16_t> ring;  // capacity_frames * 2 interleaved stereo int16
    uint32_t write_pos = 0;
    uint32_t filled = 0;
    int64_t last_write_qpc = 0;
    std::mutex buf_mtx;

    std::atomic<int64_t> last_signal_qpc{0};
    std::atomic<bool> running{false};
    std::atomic<int32_t> last_hr{S_OK};

    HANDLE stop_event = nullptr;
    HANDLE ready_event = nullptr;
    HANDLE init_done_event = nullptr;
    HANDLE thread = nullptr;
    HRESULT init_hr = E_FAIL;

    void WriteFramesFloatLocked(const float* stereo, uint32_t count) {
        if (count == 0 || capacity_frames == 0) return;
        if (count >= capacity_frames) {
            const float* tail = stereo + static_cast<size_t>(count - capacity_frames) * kChannels;
            ConvertFloatToInt16(ring.data(), tail, static_cast<size_t>(capacity_frames) * kChannels);
            write_pos = 0;
            filled = capacity_frames;
            return;
        }
        uint32_t first = capacity_frames - write_pos;
        if (first > count) first = count;
        ConvertFloatToInt16(
            ring.data() + static_cast<size_t>(write_pos) * kChannels,
            stereo,
            static_cast<size_t>(first) * kChannels);
        if (first < count) {
            uint32_t rem = count - first;
            ConvertFloatToInt16(
                ring.data(),
                stereo + static_cast<size_t>(first) * kChannels,
                static_cast<size_t>(rem) * kChannels);
        }
        write_pos = (write_pos + count) % capacity_frames;
        filled = (filled + count > capacity_frames) ? capacity_frames : (filled + count);
    }

    void WriteFramesInt16Locked(const int16_t* stereo, uint32_t count) {
        if (count == 0 || capacity_frames == 0) return;
        if (count >= capacity_frames) {
            const int16_t* tail = stereo + static_cast<size_t>(count - capacity_frames) * kChannels;
            std::memcpy(ring.data(), tail, static_cast<size_t>(capacity_frames) * kChannels * sizeof(int16_t));
            write_pos = 0;
            filled = capacity_frames;
            return;
        }
        uint32_t first = capacity_frames - write_pos;
        if (first > count) first = count;
        std::memcpy(
            ring.data() + static_cast<size_t>(write_pos) * kChannels,
            stereo,
            static_cast<size_t>(first) * kChannels * sizeof(int16_t));
        if (first < count) {
            uint32_t rem = count - first;
            std::memcpy(
                ring.data(),
                stereo + static_cast<size_t>(first) * kChannels,
                static_cast<size_t>(rem) * kChannels * sizeof(int16_t));
        }
        write_pos = (write_pos + count) % capacity_frames;
        filled = (filled + count > capacity_frames) ? capacity_frames : (filled + count);
    }

    void WriteSilenceLocked(uint32_t count) {
        if (count == 0 || capacity_frames == 0) return;
        if (count >= capacity_frames) {
            std::memset(ring.data(), 0, static_cast<size_t>(capacity_frames) * kChannels * sizeof(int16_t));
            write_pos = 0;
            filled = capacity_frames;
            return;
        }
        uint32_t first = capacity_frames - write_pos;
        if (first > count) first = count;
        std::memset(
            ring.data() + static_cast<size_t>(write_pos) * kChannels,
            0,
            static_cast<size_t>(first) * kChannels * sizeof(int16_t));
        if (first < count) {
            uint32_t rem = count - first;
            std::memset(ring.data(), 0, static_cast<size_t>(rem) * kChannels * sizeof(int16_t));
        }
        write_pos = (write_pos + count) % capacity_frames;
        filled = (filled + count > capacity_frames) ? capacity_frames : (filled + count);
    }

    void PadGapToQpcLocked(int64_t now_qpc, int64_t threshold_ms) {
        if (last_write_qpc <= 0 || now_qpc <= last_write_qpc) {
            last_write_qpc = now_qpc;
            return;
        }
        int64_t freq = QueryQpcFreq();
        double dt_s = static_cast<double>(now_qpc - last_write_qpc) / static_cast<double>(freq);
        if (dt_s * 1000.0 < static_cast<double>(threshold_ms)) {
            return;
        }
        double frames_d = dt_s * static_cast<double>(kSampleRate);
        uint32_t pad_frames = (frames_d >= static_cast<double>(capacity_frames))
            ? capacity_frames
            : static_cast<uint32_t>(frames_d);
        if (pad_frames > 0) {
            WriteSilenceLocked(pad_frames);
        }
        last_write_qpc = now_qpc;
    }
};

static HRESULT InitializeLoopbackClient(IAudioClient* client, bool* out_is_float) {
    // Attempt 1: WAVEFORMATEXTENSIBLE IEEE Float 48kHz stereo
    WAVEFORMATEXTENSIBLE wfxe{};
    wfxe.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    wfxe.Format.nChannels = kChannels;
    wfxe.Format.nSamplesPerSec = kSampleRate;
    wfxe.Format.wBitsPerSample = 32;
    wfxe.Format.nBlockAlign = (kChannels * 32) / 8;
    wfxe.Format.nAvgBytesPerSec = kSampleRate * wfxe.Format.nBlockAlign;
    wfxe.Format.cbSize = sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
    wfxe.Samples.wValidBitsPerSample = 32;
    wfxe.dwChannelMask = SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT;
    wfxe.SubFormat = kKSDATAFORMAT_SUBTYPE_IEEE_FLOAT;

    DWORD base_flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
    HRESULT hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        base_flags,
        0,
        0,
        reinterpret_cast<const WAVEFORMATEX*>(&wfxe),
        nullptr);
    if (SUCCEEDED(hr)) {
        *out_is_float = true;
        return S_OK;
    }

    // Attempt 2: Plain WAVE_FORMAT_IEEE_FLOAT 48kHz stereo
    WAVEFORMATEX wfx_f{};
    wfx_f.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
    wfx_f.nChannels = kChannels;
    wfx_f.nSamplesPerSec = kSampleRate;
    wfx_f.wBitsPerSample = 32;
    wfx_f.nBlockAlign = (kChannels * 32) / 8;
    wfx_f.nAvgBytesPerSec = kSampleRate * wfx_f.nBlockAlign;
    wfx_f.cbSize = 0;

    hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        base_flags,
        0,
        0,
        &wfx_f,
        nullptr);
    if (SUCCEEDED(hr)) {
        *out_is_float = true;
        return S_OK;
    }

    // Attempt 3: Plain 16-bit PCM 48kHz stereo (matches Microsoft's official ApplicationLoopback sample)
    WAVEFORMATEX wfx_pcm{};
    wfx_pcm.wFormatTag = WAVE_FORMAT_PCM;
    wfx_pcm.nChannels = kChannels;
    wfx_pcm.nSamplesPerSec = kSampleRate;
    wfx_pcm.wBitsPerSample = 16;
    wfx_pcm.nBlockAlign = (kChannels * 16) / 8;
    wfx_pcm.nAvgBytesPerSec = kSampleRate * wfx_pcm.nBlockAlign;
    wfx_pcm.cbSize = 0;

    hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        base_flags,
        0,
        0,
        &wfx_pcm,
        nullptr);
    if (SUCCEEDED(hr)) {
        *out_is_float = false;
        return S_OK;
    }
    return hr;
}

static DWORD WINAPI CaptureThreadProc(LPVOID param) {
    auto* sess = reinterpret_cast<ProcLoopSession*>(param);
    HRESULT hr_com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    bool com_inited = SUCCEEDED(hr_com);

    IAudioClient* client = nullptr;
    IAudioCaptureClient* capture = nullptr;
    bool is_float = true;

    HRESULT hr = ActivateProcessLoopbackClient(sess->pid, &client);
    if (SUCCEEDED(hr) && client) {
        hr = InitializeLoopbackClient(client, &is_float);
    }
    if (SUCCEEDED(hr) && client) {
        hr = client->SetEventHandle(sess->ready_event);
    }
    if (SUCCEEDED(hr) && client) {
        hr = client->GetService(kIID_IAudioCaptureClient, reinterpret_cast<void**>(&capture));
    }
    if (SUCCEEDED(hr) && client) {
        hr = client->Start();
    }

    sess->init_hr = hr;
    sess->last_hr.store(static_cast<int32_t>(hr), std::memory_order_relaxed);
    if (SUCCEEDED(hr)) {
        {
            std::lock_guard<std::mutex> lk(sess->buf_mtx);
            sess->last_write_qpc = QueryQpc();
        }
        sess->running.store(true, std::memory_order_release);
    }
    SetEvent(sess->init_done_event);

    if (SUCCEEDED(hr) && client && capture) {
        HANDLE events[2] = {sess->stop_event, sess->ready_event};

        while (true) {
            DWORD wr = WaitForMultipleObjects(2, events, FALSE, 25);
            if (wr == WAIT_OBJECT_0) {
                break;
            }
            if (wr == WAIT_TIMEOUT) {
                // Keep timeline advancing with silence when the target app pauses audio output
                std::lock_guard<std::mutex> lk(sess->buf_mtx);
                sess->PadGapToQpcLocked(QueryQpc(), 50);
                continue;
            }
            if (wr != WAIT_OBJECT_0 + 1) {
                break;
            }

            UINT32 packet_frames = 0;
            while (SUCCEEDED(capture->GetNextPacketSize(&packet_frames)) && packet_frames > 0) {
                BYTE* data = nullptr;
                UINT32 num_frames = 0;
                DWORD flags = 0;
                hr = capture->GetBuffer(&data, &num_frames, &flags, nullptr, nullptr);
                if (FAILED(hr)) {
                    sess->last_hr.store(static_cast<int32_t>(hr), std::memory_order_relaxed);
                    break;
                }

                int64_t now_qpc = QueryQpc();
                bool is_silent = (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0 || data == nullptr;
                bool has_signal = false;

                if (!is_silent && num_frames > 0) {
                    size_t total_samples = static_cast<size_t>(num_frames) * kChannels;
                    if (is_float) {
                        const float* fptr = reinterpret_cast<const float*>(data);
                        for (size_t i = 0; i < total_samples; i += 4) {
                            if (std::fabs(fptr[i]) > 0.0002f) {
                                has_signal = true;
                                break;
                            }
                        }
                        std::lock_guard<std::mutex> lk(sess->buf_mtx);
                        // Pad any large prior silence gap before appending live audio
                        sess->PadGapToQpcLocked(now_qpc, 80);
                        sess->WriteFramesFloatLocked(fptr, num_frames);
                        sess->last_write_qpc = now_qpc;
                    } else {
                        const int16_t* sptr = reinterpret_cast<const int16_t*>(data);
                        for (size_t i = 0; i < total_samples; i += 4) {
                            int v = static_cast<int>(sptr[i]);
                            if (v > 6 || v < -6) {
                                has_signal = true;
                                break;
                            }
                        }
                        std::lock_guard<std::mutex> lk(sess->buf_mtx);
                        sess->PadGapToQpcLocked(now_qpc, 80);
                        sess->WriteFramesInt16Locked(sptr, num_frames);
                        sess->last_write_qpc = now_qpc;
                    }
                } else if (num_frames > 0) {
                    std::lock_guard<std::mutex> lk(sess->buf_mtx);
                    sess->PadGapToQpcLocked(now_qpc, 80);
                    sess->WriteSilenceLocked(num_frames);
                    sess->last_write_qpc = now_qpc;
                }

                if (has_signal) {
                    sess->last_signal_qpc.store(now_qpc, std::memory_order_relaxed);
                }

                capture->ReleaseBuffer(num_frames);
            }
            if (FAILED(hr)) {
                break;
            }
        }
        client->Stop();
    }

    sess->running.store(false, std::memory_order_release);
    if (capture) capture->Release();
    if (client) client->Release();
    if (com_inited) CoUninitialize();
    return 0;
}

struct ProbeArg {
    HRESULT hr = E_FAIL;
};

static DWORD WINAPI ProbeThreadProc(LPVOID param) {
    auto* arg = reinterpret_cast<ProbeArg*>(param);
    HRESULT hr_com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    bool com_inited = SUCCEEDED(hr_com);

    IAudioClient* client = nullptr;
    HRESULT hr = ActivateProcessLoopbackClient(GetCurrentProcessId(), &client);
    if (SUCCEEDED(hr) && client) {
        bool is_float = true;
        hr = InitializeLoopbackClient(client, &is_float);
        client->Release();
    }
    arg->hr = hr;
    if (com_inited) CoUninitialize();
    return 0;
}

}  // namespace

extern "C" {

__declspec(dllexport) int procloop_is_supported(int32_t* out_hr) {
    ProbeArg arg{};
    HANDLE th = CreateThread(nullptr, 0, ProbeThreadProc, &arg, 0, nullptr);
    if (!th) {
        if (out_hr) *out_hr = HRESULT_FROM_WIN32(GetLastError());
        return 0;
    }
    DWORD wr = WaitForSingleObject(th, 4000);
    CloseHandle(th);
    if (wr != WAIT_OBJECT_0) {
        if (out_hr) *out_hr = HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        return 0;
    }
    if (out_hr) *out_hr = static_cast<int32_t>(arg.hr);
    return SUCCEEDED(arg.hr) ? 1 : 0;
}

__declspec(dllexport) void* procloop_create(uint32_t target_pid, double duration_s, int32_t* out_hr) {
    if (target_pid == 0 || duration_s <= 0.0) {
        if (out_hr) *out_hr = E_INVALIDARG;
        return nullptr;
    }

    auto* sess = new ProcLoopSession();
    sess->pid = static_cast<DWORD>(target_pid);
    uint32_t cap = ClampCapacityFrames(duration_s);
    sess->capacity_frames = cap;
    sess->ring.assign(static_cast<size_t>(cap) * kChannels, 0);

    sess->stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    sess->ready_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    sess->init_done_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!sess->stop_event || !sess->ready_event || !sess->init_done_event) {
        HRESULT hr = HRESULT_FROM_WIN32(GetLastError());
        if (out_hr) *out_hr = static_cast<int32_t>(hr);
        if (sess->stop_event) CloseHandle(sess->stop_event);
        if (sess->ready_event) CloseHandle(sess->ready_event);
        if (sess->init_done_event) CloseHandle(sess->init_done_event);
        delete sess;
        return nullptr;
    }

    sess->thread = CreateThread(nullptr, 0, CaptureThreadProc, sess, 0, nullptr);
    if (!sess->thread) {
        HRESULT hr = HRESULT_FROM_WIN32(GetLastError());
        if (out_hr) *out_hr = static_cast<int32_t>(hr);
        CloseHandle(sess->stop_event);
        CloseHandle(sess->ready_event);
        CloseHandle(sess->init_done_event);
        delete sess;
        return nullptr;
    }

    DWORD wr = WaitForSingleObject(sess->init_done_event, 4000);
    if (wr != WAIT_OBJECT_0 || FAILED(sess->init_hr)) {
        HRESULT hr = (wr != WAIT_OBJECT_0) ? HRESULT_FROM_WIN32(ERROR_TIMEOUT) : sess->init_hr;
        if (out_hr) *out_hr = static_cast<int32_t>(hr);
        SetEvent(sess->stop_event);
        WaitForSingleObject(sess->thread, 1500);
        CloseHandle(sess->thread);
        CloseHandle(sess->stop_event);
        CloseHandle(sess->ready_event);
        CloseHandle(sess->init_done_event);
        delete sess;
        return nullptr;
    }

    if (out_hr) *out_hr = S_OK;
    return sess;
}

__declspec(dllexport) uint32_t procloop_snapshot(
    void* handle,
    float* out_buf,
    uint32_t max_frames,
    uint32_t* out_sample_rate,
    int64_t* out_end_qpc,
    int64_t* out_qpc_freq) {
    if (!handle) return 0;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);

    std::lock_guard<std::mutex> lk(sess->buf_mtx);
    int64_t now_qpc = QueryQpc();
    // Pad tail silence up to now_qpc so snapshot ends at wall-clock now
    sess->PadGapToQpcLocked(now_qpc, 30);

    if (out_sample_rate) *out_sample_rate = kSampleRate;
    if (out_end_qpc) *out_end_qpc = sess->last_write_qpc;
    if (out_qpc_freq) *out_qpc_freq = QueryQpcFreq();

    uint32_t avail = sess->filled;
    if (!out_buf || max_frames == 0 || avail == 0) {
        return avail;
    }

    uint32_t take = (avail > max_frames) ? max_frames : avail;
    uint32_t cap = sess->capacity_frames;
    uint32_t start = (sess->write_pos + cap - take) % cap;

    uint32_t first = cap - start;
    if (first > take) first = take;
    constexpr float kInv32768 = 1.0f / 32768.0f;
    const int16_t* src1 = sess->ring.data() + static_cast<size_t>(start) * kChannels;
    size_t first_samples = static_cast<size_t>(first) * kChannels;
    for (size_t i = 0; i < first_samples; ++i) {
        out_buf[i] = static_cast<float>(src1[i]) * kInv32768;
    }
    if (first < take) {
        uint32_t rem = take - first;
        const int16_t* src2 = sess->ring.data();
        float* dst2 = out_buf + first_samples;
        size_t rem_samples = static_cast<size_t>(rem) * kChannels;
        for (size_t i = 0; i < rem_samples; ++i) {
            dst2[i] = static_cast<float>(src2[i]) * kInv32768;
        }
    }
    return take;
}

__declspec(dllexport) int64_t procloop_ms_since_signal(void* handle) {
    if (!handle) return -1;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    int64_t sig = sess->last_signal_qpc.load(std::memory_order_relaxed);
    if (sig <= 0) return -1;
    int64_t now = QueryQpc();
    if (now <= sig) return 0;
    return ((now - sig) * 1000LL) / QueryQpcFreq();
}

__declspec(dllexport) int procloop_is_running(void* handle) {
    if (!handle) return 0;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    return sess->running.load(std::memory_order_acquire) ? 1 : 0;
}

__declspec(dllexport) int32_t procloop_last_error(void* handle) {
    if (!handle) return E_INVALIDARG;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    return sess->last_hr.load(std::memory_order_relaxed);
}

__declspec(dllexport) uint32_t procloop_capacity_frames(void* handle) {
    if (!handle) return 0;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    std::lock_guard<std::mutex> lk(sess->buf_mtx);
    return sess->capacity_frames;
}

__declspec(dllexport) int procloop_resize(void* handle, double duration_s) {
    if (!handle || duration_s <= 0.0) return 0;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    uint32_t new_cap = ClampCapacityFrames(duration_s);

    std::lock_guard<std::mutex> lk(sess->buf_mtx);
    if (new_cap == sess->capacity_frames) {
        return 1;
    }
    uint32_t keep = (sess->filled > new_cap) ? new_cap : sess->filled;
    std::vector<int16_t> new_ring(static_cast<size_t>(new_cap) * kChannels, 0);
    if (keep > 0 && sess->capacity_frames > 0) {
        uint32_t old_cap = sess->capacity_frames;
        uint32_t start = (sess->write_pos + old_cap - keep) % old_cap;
        uint32_t first = old_cap - start;
        if (first > keep) first = keep;
        std::memcpy(
            new_ring.data(),
            sess->ring.data() + static_cast<size_t>(start) * kChannels,
            static_cast<size_t>(first) * kChannels * sizeof(int16_t));
        if (first < keep) {
            uint32_t rem = keep - first;
            std::memcpy(
                new_ring.data() + static_cast<size_t>(first) * kChannels,
                sess->ring.data(),
                static_cast<size_t>(rem) * kChannels * sizeof(int16_t));
        }
    }
    sess->ring.swap(new_ring);
    sess->capacity_frames = new_cap;
    sess->filled = keep;
    sess->write_pos = (keep < new_cap) ? keep : 0;
    return 1;
}

__declspec(dllexport) void procloop_destroy(void* handle) {
    if (!handle) return;
    auto* sess = reinterpret_cast<ProcLoopSession*>(handle);
    if (sess->stop_event) {
        SetEvent(sess->stop_event);
    }
    if (sess->thread) {
        WaitForSingleObject(sess->thread, 2500);
        CloseHandle(sess->thread);
    }
    if (sess->stop_event) CloseHandle(sess->stop_event);
    if (sess->ready_event) CloseHandle(sess->ready_event);
    if (sess->init_done_event) CloseHandle(sess->init_done_event);
    delete sess;
}

}  // extern "C"
