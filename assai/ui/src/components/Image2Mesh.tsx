import { useState, useEffect } from 'react';
import { Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import ModelSettingsForm, { SettingInputFieldSpec } from './ModelSettingsForm';
import { Message } from '../services/types';
import { assaiAPI } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Image2Mesh = () => {
    const { sessionId } = useWebSocket();
    const [settingsSpec, setSettingsSpec] = useState<SettingInputFieldSpec[] | null>(null);
    const [isLoadingSpec, setIsLoadingSpec] = useState<boolean>(true);

    let settings: any = {}
    function setSetting(key: string, value: any) {
        settings[key] = value
    }

    // Fetch settings spec on mount
    useEffect(() => {
        const fetchSpec = async () => {
            try {
                setIsLoadingSpec(true);
                const spec = await assaiAPI.getModelSettingsSpec('image2mesh');
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
        // Extract image data URL from message
        const imageDataUrl = typeof userMessage.content === 'object' && userMessage.content.kind === 'image'
            ? userMessage.content.data
            : '';

        return await assaiAPI.generateMesh(
            imageDataUrl,
            settings, // Use settings from ModelSettingsForm
            undefined,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const settingsPanel = settingsSpec ? (
        <ModelSettingsForm
            spec={settingsSpec}
            onSettingsChange={setSetting}
        />
    ) : isLoadingSpec ? (
        <Text fontSize="sm" color="gray.400" p={4}>Loading settings...</Text>
    ) : null;

    const config: ChatComponentConfig = {
        title: 'Image to 3D Mesh',
        description: 'Upload an image and I\'ll generate a 3D model from it. Each image will generate a new 3D mesh.',
        allowedInputTypes: ['image'],
        expectedOutputType: 'mesh',
        placeholder: 'Upload an image to generate a 3D model...',
        emptyStateTitle: 'Image to 3D Mesh',
        emptyStateDescription: 'Upload an image and I\'ll generate a 3D model from it. Each image will generate a new 3D mesh.',
        emptyStateExamples: [],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default Image2Mesh;

