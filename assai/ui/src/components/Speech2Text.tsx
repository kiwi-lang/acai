import { useState, useEffect, useRef, useCallback } from 'react';
import {
    Box,
    VStack,
    HStack,
    Text,
    Button,
    IconButton,
} from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import LogDisplay from './LogDisplay';
import { Message } from '../services/types';
import { assaiAPI, SpeechRecognitionParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const MicIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
);

const StopIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
);

const Speech2Text = () => {
    const { socket, sessionId } = useWebSocket();
    const [messages, setMessages] = useState<Message[]>([]);
    const [isRecording, setIsRecording] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [recordedAudioUrl, setRecordedAudioUrl] = useState<string | null>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const audioChunksRef = useRef<Blob[]>([]);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const actionIdCounterRef = useRef<number>(0);
    const actionIdToMessageIdRef = useRef<Map<number, string>>(new Map());

    // Recognition parameters
    const [recognitionParams] = useState<SpeechRecognitionParams>({
        language: null, // Auto-detect
        task: 'transcribe',
    });

    // Memoize handlers to prevent duplicate listeners
    const handleStdout = useCallback((data: { id: number; line: string }) => {
        const messageId = actionIdToMessageIdRef.current.get(data.id);
        if (messageId) {
            setMessages(prev => prev.map(msg => {
                if (msg.id === messageId) {
                    return {
                        ...msg,
                        logs: [
                            ...(msg.logs || []),
                            { type: 'stdout' as const, line: data.line, timestamp: new Date() }
                        ]
                    };
                }
                return msg;
            }));
        }
    }, []);

    const handleStderr = useCallback((data: { id: number; line: string }) => {
        const messageId = actionIdToMessageIdRef.current.get(data.id);
        if (messageId) {
            setMessages(prev => prev.map(msg => {
                if (msg.id === messageId) {
                    return {
                        ...msg,
                        logs: [
                            ...(msg.logs || []),
                            { type: 'stderr' as const, line: data.line, timestamp: new Date() }
                        ]
                    };
                }
                return msg;
            }));
        }
    }, []);

    useEffect(() => {
        document.title = 'Speech to Text - ASSAI';

        if (!socket) {
            return;
        }

        // Set up log listeners for stdout/stderr
        socket.off('stdout', handleStdout);
        socket.off('stderr', handleStderr);
        socket.on('stdout', handleStdout);
        socket.on('stderr', handleStderr);

        // Cleanup on unmount
        return () => {
            if (socket) {
                socket.off('stdout', handleStdout);
                socket.off('stderr', handleStderr);
            }
            // Stop recording if still active
            if (mediaRecorderRef.current && isRecording) {
                mediaRecorderRef.current.stop();
            }
        };
    }, [socket, handleStdout, handleStderr, isRecording]);

    useEffect(() => {
        // Scroll to bottom when new messages arrive
        // Scroll to bottom when new messages arrive, but don't steal focus
        if (messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [messages]);

    const startRecording = async () => {
        try {
            // Request microphone permission - this will trigger browser prompt
            // Following MDN example: https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Build_a_phone_with_peerjs/Connect_peers/Get_microphone_permission
            console.log('Requesting microphone permission...');

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                console.error('navigator.mediaDevices.getUserMedia is not available');
                alert('Microphone access requires a secure context (HTTPS or localhost). Please ensure you are accessing the site via HTTPS or localhost.');
                return;
            }

            const stream = await navigator.mediaDevices.getUserMedia({
                video: false,
                audio: true
            });

            console.log('Microphone access granted');

            // Try to find a supported MIME type
            let mimeType = 'audio/webm;codecs=opus';
            if (!MediaRecorder.isTypeSupported(mimeType)) {
                mimeType = 'audio/webm';
                if (!MediaRecorder.isTypeSupported(mimeType)) {
                    mimeType = 'audio/mp4';
                    if (!MediaRecorder.isTypeSupported(mimeType)) {
                        mimeType = ''; // Use default
                    }
                }
            }

            const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

            audioChunksRef.current = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunksRef.current.push(event.data);
                }
            };

            mediaRecorder.onstop = async () => {
                // Stop all tracks
                stream.getTracks().forEach(track => track.stop());

                // Create audio URL for playback
                const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
                const audioUrl = URL.createObjectURL(audioBlob);

                // Add user message with recorded audio
                const userMessageId = Date.now().toString();
                const userMessage: Message = {
                    id: userMessageId,
                    role: 'user',
                    content: '',
                    timestamp: new Date(),
                    type: 'audio',
                    audioUrl: audioUrl,
                };
                setMessages(prev => [...prev, userMessage]);

                // Convert to WAV and send for transcription
                await processAudio(audioBlob, audioUrl);
            };

            mediaRecorder.onerror = (event) => {
                console.error('MediaRecorder error:', event);
                setIsRecording(false);
                alert('Error recording audio. Please try again.');
            };

            mediaRecorderRef.current = mediaRecorder;
            mediaRecorder.start(100); // Collect data every 100ms
            setIsRecording(true);
        } catch (error: any) {
            console.error('Error accessing microphone:', error);
            setIsRecording(false);
            let errorMessage = 'Could not access microphone. ';

            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                errorMessage += 'Please allow microphone access in your browser settings and try again.';
            } else if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
                errorMessage += 'No microphone found. Please connect a microphone.';
            } else if (error.name === 'NotReadableError' || error.name === 'TrackStartError') {
                errorMessage += 'Microphone is already in use by another application.';
            } else {
                errorMessage += `Error: ${error.message || 'Unknown error'}`;
            }

            alert(errorMessage);
        }
    };

    const stopRecording = () => {
        if (mediaRecorderRef.current && isRecording) {
            mediaRecorderRef.current.stop();
            setIsRecording(false);
        }
    };

    const blobToBase64Wav = async (blob: Blob): Promise<string> => {
        // Convert WebM/Opus to WAV using Web Audio API
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = async () => {
                try {
                    const arrayBuffer = reader.result as ArrayBuffer;
                    const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
                    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

                    // Convert to WAV
                    const wav = audioBufferToWav(audioBuffer);
                    const base64 = btoa(String.fromCharCode(...new Uint8Array(wav)));
                    resolve(`data:audio/wav;base64,${base64}`);
                } catch (error) {
                    // Fallback: send as WebM if conversion fails
                    // Convert ArrayBuffer to base64
                    const arrayBuffer = reader.result as ArrayBuffer;
                    const uint8Array = new Uint8Array(arrayBuffer);
                    const base64 = btoa(String.fromCharCode(...uint8Array));
                    resolve(`data:audio/webm;base64,${base64}`);
                }
            };
            reader.onerror = reject;
            reader.readAsArrayBuffer(blob);
        });
    };

    const audioBufferToWav = (buffer: AudioBuffer): ArrayBuffer => {
        const length = buffer.length;
        const numberOfChannels = buffer.numberOfChannels;
        const sampleRate = buffer.sampleRate;
        const arrayBuffer = new ArrayBuffer(44 + length * numberOfChannels * 2);
        const view = new DataView(arrayBuffer);
        const channels: Float32Array[] = [];
        let offset = 0;
        let pos = 0;

        // Write WAV header
        const setUint16 = (data: number) => {
            view.setUint16(pos, data, true);
            pos += 2;
        };
        const setUint32 = (data: number) => {
            view.setUint32(pos, data, true);
            pos += 4;
        };

        // RIFF identifier
        setUint32(0x46464952); // "RIFF"
        setUint32(36 + length * numberOfChannels * 2); // File length - 8
        setUint32(0x45564157); // "WAVE"
        setUint32(0x20746d66); // "fmt " chunk
        setUint32(16); // fmt chunk length
        setUint16(1); // audio format (1 = PCM)
        setUint16(numberOfChannels);
        setUint32(sampleRate);
        setUint32(sampleRate * numberOfChannels * 2); // byte rate
        setUint16(numberOfChannels * 2); // block align
        setUint16(16); // bits per sample
        setUint32(0x61746164); // "data" chunk
        setUint32(length * numberOfChannels * 2);

        // Get channel data
        for (let i = 0; i < numberOfChannels; i++) {
            channels.push(buffer.getChannelData(i));
        }

        // Interleave channels
        for (let i = 0; i < length; i++) {
            for (let channel = 0; channel < numberOfChannels; channel++) {
                let sample = Math.max(-1, Math.min(1, channels[channel][i]));
                sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
                view.setInt16(pos, sample, true);
                pos += 2;
            }
        }

        return arrayBuffer;
    };

    const processAudio = async (audioBlob: Blob, audioUrl: string) => {
        if (!audioBlob) {
            return;
        }

        // Generate unique action ID for this request
        const actionId = ++actionIdCounterRef.current;
        const assistantMessageId = (Date.now() + 1).toString();

        // Create placeholder message for logs/transcription
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: ``,
            timestamp: new Date(),
            type: 'text',
            actionId,
            logs: [],
            isGenerating: true,
        };
        setMessages(prev => [...prev, assistantMessage]);

        // Map action ID to message ID for log routing
        actionIdToMessageIdRef.current.set(actionId, assistantMessageId);

        setIsProcessing(true);

        try {
            // Convert blob to base64
            const audioDataUri = await blobToBase64Wav(audioBlob);

            // Transcribe audio
            const response = await assaiAPI.transcribeSpeech(
                audioDataUri,
                recognitionParams,
                undefined,
                sessionId ?? undefined,
                actionId
            );

            // Handle HTTP response - replace logs with transcribed text
            if (response && response.text) {
                setMessages(prev => prev.map(msg => {
                    if (msg.id === assistantMessageId) {
                        return {
                            ...msg,
                            content: response.text,
                            logs: undefined, // Remove logs when text is ready
                            isGenerating: false,
                        };
                    }
                    return msg;
                }));
            } else {
                throw new Error('No text data received from server');
            }

            setIsProcessing(false);

            // Clean up action ID mapping after a delay
            setTimeout(() => {
                actionIdToMessageIdRef.current.delete(actionId);
            }, 5000);
        } catch (error) {
            console.error('Failed to transcribe speech:', error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to transcribe speech';

            setIsProcessing(false);

            // Update the placeholder message with error
            setMessages(prev => prev.map(msg => {
                if (msg.id === assistantMessageId) {
                    return {
                        ...msg,
                        content: `Sorry, I encountered an error: ${errorMessage}. Please try again.`,
                        type: 'text',
                        logs: undefined,
                        isGenerating: false,
                    };
                }
                return msg;
            }));

            // Clean up action ID mapping
            actionIdToMessageIdRef.current.delete(actionId);
        } finally {
            // Clean up audio URL after processing
            URL.revokeObjectURL(audioUrl);
            audioChunksRef.current = [];
            setRecordedAudioUrl(null);
        }
    };

    const EmptyState = () => (
        <VStack
            flex={1}
            justify="center"
            align="center"
            p={8}
            gap={6}
        >
            <Box
                w="64px"
                h="64px"
                bg="orange.500"
                borderRadius="xl"
                display="flex"
                alignItems="center"
                justifyContent="center"
                fontSize="2xl"
                color="white"
                fontWeight="bold"
            >
                🎤
            </Box>

            <VStack gap={2} textAlign="center">
                <Text fontSize="2xl" fontWeight="semibold" color="white">
                    Speech to Text
                </Text>
                <Text fontSize="md" color="gray.400" maxW="md">
                    Click the microphone button to start recording. Your speech will be transcribed to text.
                </Text>
            </VStack>
        </VStack>
    );

    return (
        <Box
            display="flex"
            flexDirection="column"
            h="100vh"
            w="100%"
            bg="gray.900"
            overflow="hidden"
        >
            {/* Messages Area */}
            <Box
                flex={1}
                overflowY="auto"
                w="100%"
                minH={0}
            >
                {messages.length === 0 ? (
                    <EmptyState />
                ) : (
                    <VStack gap={0} w="100%">
                        {messages.map((message) => (
                            <ChatMessage
                                key={message.id}
                                message={message}
                            />
                        ))}


                        <div ref={messagesEndRef} />
                    </VStack>
                )}
            </Box>

            {/* Recording Controls */}
            <Box
                borderTop="1px solid"
                borderColor="gray.700"
                p={4}
                display="flex"
                justifyContent="center"
                alignItems="center"
            >
                <VStack gap={4} w="100%" maxW="md">
                    {isRecording ? (
                        <>
                            <HStack gap={4}>
                                <Box
                                    w="16px"
                                    h="16px"
                                    bg="red.500"
                                    borderRadius="full"
                                    animation="pulse 1.5s ease-in-out infinite"
                                />
                                <Text color="red.400" fontWeight="semibold">
                                    Recording...
                                </Text>
                            </HStack>
                            <Button
                                size="lg"
                                colorScheme="red"
                                onClick={stopRecording}
                                leftIcon={<StopIcon />}
                                disabled={isProcessing}
                            >
                                Stop Recording
                            </Button>
                        </>
                    ) : (
                        <Button
                            size="lg"
                            colorScheme="orange"
                            onClick={startRecording}
                            leftIcon={<MicIcon />}
                            disabled={isProcessing || isRecording}
                        >
                            {isProcessing ? 'Processing...' : 'Start Recording'}
                        </Button>
                    )}
                </VStack>
            </Box>

            {/* Log Display - Separate from chat area, collapsible at bottom */}
            <LogDisplay />
        </Box>
    );
};

export default Speech2Text;

