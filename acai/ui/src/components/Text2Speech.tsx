import { useState } from 'react';
import { VStack, Input, Button, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { acaiAPI, SpeechGenerationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Speech = () => {
    const { sessionId } = useWebSocket();

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<SpeechGenerationParams>({
        speed: 1.0,
        pitch: 0.0,
        sample_rate: 22050,
    });

    const resetToDefaults = () => {
        setGenerationParams({
            speed: 1.0,
            pitch: 0.0,
            sample_rate: 22050,
        });
    };

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        return await acaiAPI.generateSpeech(
            typeof userMessage.content === 'object' && userMessage.content.kind === 'text'
                ? userMessage.content.data
                : '',
            generationParams,
            undefined,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const settingsPanel = (
        <VStack gap={4} align="stretch">
            {/* Speed */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Speed: {generationParams.speed?.toFixed(1)}x
                </Text>
                <Input
                    type="number"
                    value={generationParams.speed}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 1.0;
                        setGenerationParams(prev => ({ ...prev, speed: value }));
                    }}
                    min={0.5}
                    max={2.0}
                    step={0.1}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Pitch */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Pitch: {generationParams.pitch?.toFixed(1)} semitones
                </Text>
                <Input
                    type="number"
                    value={generationParams.pitch}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 0.0;
                        setGenerationParams(prev => ({ ...prev, pitch: value }));
                    }}
                    min={-12.0}
                    max={12.0}
                    step={0.5}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Sample Rate */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Sample Rate (Hz)</Text>
                <Input
                    type="number"
                    value={generationParams.sample_rate}
                    onChange={(e) => {
                        const value = parseInt(e.target.value) || 22050;
                        setGenerationParams(prev => ({ ...prev, sample_rate: value }));
                    }}
                    min={8000}
                    max={48000}
                    step={1000}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                />
            </VStack>

            <Button
                size="sm"
                variant="outline"
                onClick={resetToDefaults}
                w="100%"
                color="gray.200"
                borderColor="gray.600"
                _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
            >
                Reset to Defaults
            </Button>
        </VStack>
    );

    const config: ChatComponentConfig = {
        title: 'Text to Speech',
        description: 'Enter text and I\'ll convert it to speech. Each prompt will generate a new audio file.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'audio',
        placeholder: 'Enter text to convert to speech...',
        emptyStateTitle: 'Text to Speech',
        emptyStateDescription: 'Enter text and I\'ll convert it to speech. Each prompt will generate a new audio file.',
        emptyStateExamples: [
            'Hello, this is a test of the text to speech system.',
            'The quick brown fox jumps over the lazy dog.',
            'Welcome to Açaí, your AI assistant platform.',
            'Text to speech conversion is now available.',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default Text2Speech;
