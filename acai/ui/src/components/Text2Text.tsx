import { useState, useEffect } from 'react';
import { HStack, Input, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import ModelSettingsForm, { ModelSettingsSpec } from './ModelSettingsForm';
import { Message } from '../services/types';
import { acaiAPI } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Text = () => {
    const { sessionId } = useWebSocket();
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [selectedModel, setSelectedModel] = useState<string | null>(null);
    const [customModel, setCustomModel] = useState<string>('');
    const [useCustomModel, setUseCustomModel] = useState<boolean>(false);
    const [isLoadingModels, setIsLoadingModels] = useState<boolean>(true);
    const [settingsSpec, setSettingsSpec] = useState<ModelSettingsSpec | null>(null);
    const [isLoadingSpec, setIsLoadingSpec] = useState<boolean>(true);
    const [modelSettings, setModelSettings] = useState<Record<string, number>>({});

    // Fetch available models on mount
    useEffect(() => {
        const fetchModels = async () => {
            try {
                setIsLoadingModels(true);
                const models = await acaiAPI.listText2TextModels();
                setAvailableModels(models);
                if (models.length > 0 && !selectedModel) {
                    setSelectedModel(models[0]);
                }
            } catch (error) {
                console.error('Failed to fetch models:', error);
            } finally {
                setIsLoadingModels(false);
            }
        };
        fetchModels();
    }, []);

    // Fetch settings spec when model changes
    useEffect(() => {
        const fetchSpec = async () => {
            const modelToUse = useCustomModel && customModel.trim()
                ? customModel.trim()
                : selectedModel;

            if (!modelToUse) {
                setIsLoadingSpec(false);
                return;
            }

            try {
                setIsLoadingSpec(true);
                const spec = await acaiAPI.getModelSettingsSpec('text2text', modelToUse);
                setSettingsSpec(spec);
            } catch (error) {
                console.error('Failed to fetch settings spec:', error);
                setSettingsSpec(null);
            } finally {
                setIsLoadingSpec(false);
            }
        };

        fetchSpec();
    }, [selectedModel, customModel, useCustomModel]);

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        // Determine which model to use
        const modelToUse = useCustomModel && customModel.trim()
            ? customModel.trim()
            : selectedModel || undefined;

        return await acaiAPI.generateText(
            typeof userMessage.content === 'object' && userMessage.content.kind === 'text'
                ? userMessage.content.data
                : '',
            modelSettings, // Use settings from ModelSettingsForm
            modelToUse,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const modelToUse = useCustomModel && customModel.trim()
        ? customModel.trim()
        : selectedModel;

    const settingsPanel = settingsSpec ? (
        <ModelSettingsForm
            spec={settingsSpec}
            taskType="text2text"
            modelName={modelToUse || undefined}
            onSettingsChange={setModelSettings}
        />
    ) : isLoadingSpec ? (
        <Text fontSize="sm" color="gray.400" p={4}>Loading settings...</Text>
    ) : null;

    // Model selector component
    const modelSelector = (
        <HStack gap={3} align="center" w="100%" maxW="48rem" mx="auto">
            <Text fontSize="sm" fontWeight="medium" color="gray.300" minW="fit-content">
                Model:
            </Text>
            <select
                value={useCustomModel ? 'custom' : (selectedModel || '')}
                onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
                    if (e.target.value === 'custom') {
                        setUseCustomModel(true);
                    } else {
                        setUseCustomModel(false);
                        setSelectedModel(e.target.value);
                    }
                }}
                disabled={isLoadingModels}
                style={{
                    flex: 1,
                    maxWidth: '300px',
                    padding: '6px 12px',
                    borderRadius: '6px',
                    border: '1px solid #4A5568',
                    fontSize: '14px',
                    backgroundColor: '#2D3748',
                    color: '#E2E8F0',
                    cursor: isLoadingModels ? 'not-allowed' : 'pointer',
                }}
            >
                {availableModels.map((model) => (
                    <option key={model} value={model} style={{ backgroundColor: '#1a202c', color: '#e2e8f0' }}>
                        {model}
                    </option>
                ))}
                <option value="custom" style={{ backgroundColor: '#1a202c', color: '#e2e8f0' }}>
                    Custom Model...
                </option>
            </select>
            {useCustomModel && (
                <Input
                    placeholder="Enter model name (e.g., mistralai/Mistral-7B-Instruct-v0.2)"
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    size="sm"
                    bg="gray.800"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.800' }}
                    flex={1}
                    maxW="400px"
                />
            )}
        </HStack>
    );

    const config: ChatComponentConfig = {
        title: 'Text to Text',
        description: 'Have a conversation with an AI language model. Ask questions, get answers, and explore AI-generated text.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'text',
        placeholder: 'Start a conversation...',
        emptyStateTitle: 'Text to Text',
        emptyStateDescription: 'Have a conversation with an AI language model. Ask questions, get answers, and explore AI-generated text.',
        emptyStateExamples: [
            'Tell me a short story about a robot',
            'Explain quantum computing in simple terms',
            'Write a poem about the ocean',
            'What are the benefits of renewable energy?',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
        modelSelector,
    };

    return <ChatComponent config={config} />;
};

export default Text2Text;
