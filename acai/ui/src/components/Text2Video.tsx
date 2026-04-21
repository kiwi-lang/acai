import { useState, useEffect } from 'react';
import { Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import ModelSettingsForm, { ModelSettingsSpec } from './ModelSettingsForm';
import { Message } from '../services/types';
import { acaiAPI } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Video = () => {
    const { sessionId } = useWebSocket();
    const [settingsSpec, setSettingsSpec] = useState<ModelSettingsSpec | null>(null);
    const [isLoadingSpec, setIsLoadingSpec] = useState<boolean>(true);
    const [modelSettings, setModelSettings] = useState<Record<string, number>>({});

    // Fetch settings spec on mount
    useEffect(() => {
        const fetchSpec = async () => {
            try {
                setIsLoadingSpec(true);
                const spec = await acaiAPI.getModelSettingsSpec('text2video');
                setSettingsSpec(spec);
            } catch (error) {
                console.error('Failed to fetch settings spec:', error);
                setSettingsSpec(null);
            } finally {
                setIsLoadingSpec(false);
            }
        };

        fetchSpec();
    }, []);

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        return await acaiAPI.generateVideo(
            typeof userMessage.content === 'object' && userMessage.content.kind === 'text'
                ? userMessage.content.data
                : '',
            modelSettings, // Use settings from ModelSettingsForm
            undefined,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const settingsPanel = settingsSpec ? (
        <ModelSettingsForm
            spec={settingsSpec}
            taskType="text2video"
            onSettingsChange={setModelSettings}
        />
    ) : isLoadingSpec ? (
        <Text fontSize="sm" color="gray.400" p={4}>Loading settings...</Text>
    ) : null;

    const config: ChatComponentConfig = {
        title: 'Text to Video',
        description: 'Describe the video you want to generate, and I\'ll create it for you. Each prompt will generate a new video.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'video',
        placeholder: 'Describe the video you want to generate...',
        emptyStateTitle: 'Text to Video',
        emptyStateDescription: 'Describe the video you want to generate, and I\'ll create it for you. Each prompt will generate a new video.',
        emptyStateExamples: [
            'A cat playing in a garden',
            'A sunset over the ocean',
            'A futuristic city with flying cars',
            'A peaceful forest scene with birds',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default Text2Video;

