import { useState, useEffect } from 'react';
import { VStack, Text, Input } from '@chakra-ui/react';


interface SettingInputFieldSpec {
    name: string
    type: string
    min: number
    max: number
    default: any
}



interface SettingInputFieldProps {
    spec: SettingInputFieldSpec
    onBlur: (key: string, value: any) => void
}



const SettingInputField = ({ spec, onBlur }: SettingInputFieldProps) => {
    const [value, setValue] = useState(spec.default);

    // Format field name: convert snake_case to Title Case
    const displayName = spec.name
        .split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ');

    // // Initialize settings on mount with default value
    // useEffect(() => {
    //     onBlur(spec.name, spec.default);
    // }, []); // Empty dependency array - only run on mount

    function onChange(e: React.ChangeEvent<HTMLInputElement>) {
        const stringValue = e.target.value;
        setValue(stringValue);
    }

    function handleBlur() {
        // Convert string value to appropriate type before calling onBlur
        let typedValue: number;

        if (spec.type === 'int') {
            typedValue = parseInt(value as string, 10);
            if (isNaN(typedValue)) {
                typedValue = spec.default;
            }
        } else {
            typedValue = parseFloat(value as string);
            if (isNaN(typedValue)) {
                typedValue = spec.default;
            }
        }

        // Apply min/max constraints
        if (spec.min !== null && spec.min !== undefined && typedValue < spec.min) {
            typedValue = spec.min;
        }
        if (spec.max !== null && spec.max !== undefined && typedValue > spec.max) {
            typedValue = spec.max;
        }

        // Update local state with typed value
        setValue(typedValue);
        onBlur(spec.name, typedValue);
    }

    return <VStack align="flex-start" gap={1}>
        <Text fontSize="sm" fontWeight="medium" color="gray.300">
            {displayName}
        </Text>
        <Input
            type={spec.type === 'int' ? 'number' : 'number'}
            step={spec.type === 'float' ? 'any' : '1'}
            value={value}
            onChange={onChange}
            onBlur={handleBlur}
            min={spec.min ?? undefined}
            max={spec.max ?? undefined}
            size="sm"
            bg="gray.700"
            borderColor="gray.600"
            color="gray.100"
            _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
        />
        {(spec.min !== null || spec.max !== null) && (
            <Text fontSize="xs" color="gray.500">
                {spec.min !== null && spec.max !== null
                    ? `Range: ${spec.min} - ${spec.max}`
                    : spec.min !== null
                        ? `Min: ${spec.min}`
                        : `Max: ${spec.max}`
                }
            </Text>
        )}
    </VStack>
}


interface ModelSettingsFormProps {
    spec: SettingInputFieldSpec[];
    onSettingsChange: (key: string, value: any) => void;
}

const ModelSettingsForm = ({ spec, onSettingsChange }: ModelSettingsFormProps) => {

    return (
        <VStack gap={4} align="stretch" p={4}>
            {
                spec.map((field) => {
                    return <SettingInputField spec={field} onBlur={onSettingsChange}></SettingInputField>
                })
            }
        </VStack>
    );
};

export default ModelSettingsForm;

