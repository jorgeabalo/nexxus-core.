/* AudioQueue input + PCM WAV. APIs available in Mac OS X 10.6.
 * Build on the legacy Intel SDK. No network, no third-party audio library. */
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>

typedef struct {
    AudioFileID file;
    SInt64 packet;
    OSStatus error;
    int running;
} Recording;

static void receive(void *data, AudioQueueRef queue, AudioQueueBufferRef buffer,
                    const AudioTimeStamp *time, UInt32 packets,
                    const AudioStreamPacketDescription *descriptions) {
    Recording *r = (Recording *)data;
    (void)time; (void)descriptions;
    if (!r->running || r->error) return;
    packets = buffer->mAudioDataByteSize / 2;
    r->error = AudioFileWritePackets(r->file, false, buffer->mAudioDataByteSize,
                                    NULL, r->packet, &packets, buffer->mAudioData);
    r->packet += packets;
    if (!r->error) r->error = AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
}

int main(int argc, char **argv) {
    Recording r;
    AudioStreamBasicDescription format;
    AudioQueueRef queue = NULL;
    CFURLRef url;
    int i, fd, result = 1;
    char *end;
    long seconds;
    if (argc != 3) return 2;
    seconds = strtol(argv[2], &end, 10);
    if (*end || seconds < 1 || seconds > 30) return 2;
    /* Reserve a new private file. Refuse overwrite and symlinks. */
    fd = open(argv[1], O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return 3;
    close(fd);
    memset(&r, 0, sizeof(r));
    memset(&format, 0, sizeof(format));
    format.mSampleRate = 16000;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked;
    format.mBytesPerPacket = format.mBytesPerFrame = 2;
    format.mFramesPerPacket = 1;
    format.mChannelsPerFrame = 1;
    format.mBitsPerChannel = 16;
    url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)argv[1], strlen(argv[1]), false);
    if (!url) goto cleanup;
    r.error = AudioFileCreateWithURL(url, kAudioFileWAVEType, &format,
                                    kAudioFileFlags_EraseFile, &r.file);
    CFRelease(url);
    if (r.error) goto cleanup;
    r.error = AudioQueueNewInput(&format, receive, &r, CFRunLoopGetCurrent(),
                                kCFRunLoopCommonModes, 0, &queue);
    if (r.error) goto cleanup;
    for (i = 0; i < 3; i++) {
        AudioQueueBufferRef buffer;
        r.error = AudioQueueAllocateBuffer(queue, 3200, &buffer);
        if (r.error) goto cleanup;
        r.error = AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
        if (r.error) goto cleanup;
    }
    r.running = 1;
    r.error = AudioQueueStart(queue, NULL);
    if (!r.error) {
        CFAbsoluteTime until = CFAbsoluteTimeGetCurrent() + seconds;
        while (!r.error && CFAbsoluteTimeGetCurrent() < until)
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, false);
    }
    if (!r.error && r.packet > 0) result = 0;
cleanup:
    r.running = 0;
    if (queue) { AudioQueueStop(queue, true); AudioQueueDispose(queue, true); }
    if (r.file && AudioFileClose(r.file)) result = 1;
    if (result) { unlink(argv[1]); fprintf(stderr, "Audio capture failed. Check default input device.\n"); }
    return result;
}
