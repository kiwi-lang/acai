import { useState, useEffect, useCallback } from 'react';
import { assaiAPI } from '../services/api';
import { ModelSettingsSpec } from '../components/ModelSettingsForm';

export interface ModelSettings {
    [key: string]: number;
}

export const useModelSettings = (
    taskType: 'text2image' | 'text2text' | 'text2video' | 'image2mesh' | 'text2speech' | 'speech2text' | 'depth_estimation',
    modelName: string | null,
    spec?: ModelSettingsSpec | null
) => {
    const [settings, setSettings] = useState<ModelSettings>({});
    const [isLoading, setIsLoading] = useState(false);

    // Load settings when model or spec changes
    useEffect(() => {
        const loadSettings = async () => {
            if (!modelName) {
                setSettings({});
                return;
            }

            try {
                setIsLoading(true);
                const savedSettings = await assaiAPI.getModelSettings(taskType, modelName);
                if (savedSettings && Object.keys(savedSettings).length > 0) {
                    setSettings(savedSettings);
                } else if (spec) {
                    // If no saved settings, use defaults from spec
                    const defaultSettings: ModelSettings = {};
                    Object.entries(spec).forEach(([key, fieldSpec]) => {
                        defaultSettings[key] = fieldSpec.default;
                    });
                    setSettings(defaultSettings);
                }
            } catch (error) {
                console.error('Failed to load model settings:', error);
                // Fallback to defaults from spec if available
                if (spec) {
                    const defaultSettings: ModelSettings = {};
                    Object.entries(spec).forEach(([key, fieldSpec]) => {
                        defaultSettings[key] = fieldSpec.default;
                    });
                    setSettings(defaultSettings);
                }
            } finally {
                setIsLoading(false);
            }
        };

        loadSettings();
    }, [modelName, taskType, spec]);

    const updateSettings = useCallback((newSettings: ModelSettings) => {
        setSettings(newSettings);
    }, []);

    const getSettings = useCallback(() => {
        return settings;
    }, [settings]);

    return {
        settings,
        isLoading,
        updateSettings,
        getSettings,
    };
};

