import { useState, useEffect } from 'react';
import { Box, HStack, Text, VStack } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';

interface TelemetryData {
    cpu: {
        memory: [number, number];
        load: number;
    };
    gpu: Record<string, {
        memory: [number, number];
        load: number;
        temp: number;
        power: number;
    }>;
}

interface SparklineProps {
    values: number[];
    color: string;
    width?: number;
    height?: number;
}

const Sparkline = ({ values, color, width = 60, height = 20 }: SparklineProps) => {
    if (values.length === 0) {
        return <Box w={width} h={height} />;
    }

    const padding = 2;
    const graphWidth = width - padding * 2;
    const graphHeight = height - padding * 2;

    // Normalize values to 0-100 range (fixed scale)
    const minValue = 0;
    const maxValue = 100;
    const range = maxValue - minValue;

    const points = values.map((value, index) => {
        const x = padding + (index / (values.length - 1 || 1)) * graphWidth;
        // Clamp value between 0 and 100, then normalize to 0-1
        const clampedValue = Math.max(0, Math.min(100, value));
        const normalizedValue = (clampedValue - minValue) / range;
        const y = padding + graphHeight - (normalizedValue * graphHeight);
        return `${x},${y}`;
    }).join(' ');

    return (
        <Box flexShrink={0} className="PLOT">
            <svg width={width} height={height} style={{ display: 'block' }}>
                <polyline
                    points={points}
                    fill="none"
                    stroke={color}
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                />
            </svg>
        </Box>
    );
};

const TelemetryDisplay = () => {
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [cpuHistory, setCpuHistory] = useState<number[]>([]);
    const [gpuHistory, setGpuHistory] = useState<number[]>([]);
    const [gpuMemoryHistory, setGpuMemoryHistory] = useState<number[]>([]);
    const maxHistoryLength = 60;

    useEffect(() => {
        const fetchTelemetry = async () => {
            try {
                const data = await assaiAPI.getTelemetry();
                setTelemetry(data);
                setError(null);

                // Update history
                const cpuLoad = data.cpu.load * 100;
                setCpuHistory(prev => {
                    const updated = [...prev, cpuLoad];
                    return updated.slice(-maxHistoryLength);
                });

                // Get first GPU
                const gpuEntries = Object.entries(data.gpu);
                const firstGpu = gpuEntries.length > 0 ? gpuEntries[0][1] : null;

                if (firstGpu) {
                    const gpuLoad = firstGpu.load * 100;
                    setGpuHistory(prev => {
                        const updated = [...prev, gpuLoad];
                        return updated.slice(-maxHistoryLength);
                    });

                    const gpuMemoryPercent = (firstGpu.memory[0] / firstGpu.memory[1]) * 100;
                    setGpuMemoryHistory(prev => {
                        const updated = [...prev, gpuMemoryPercent];
                        return updated.slice(-maxHistoryLength);
                    });
                }
            } catch (err) {
                console.error('Failed to fetch telemetry:', err);
                setError('Failed to load');
            }
        };

        // Fetch immediately
        fetchTelemetry();

        // Then fetch every second
        const interval = setInterval(fetchTelemetry, 1000);

        return () => clearInterval(interval);
    }, []);

    if (error || !telemetry) {
        return null;
    }

    // Get first GPU (or use all GPUs if multiple)
    const gpuEntries = Object.entries(telemetry.gpu);
    const firstGpu = gpuEntries.length > 0 ? gpuEntries[0][1] : null;

    // Calculate GPU memory percentage
    const gpuMemoryPercent = firstGpu
        ? (firstGpu.memory[0] / firstGpu.memory[1]) * 100
        : 0;

    return (
        <Box
            w="100%"
            pt={3}
            borderTop="1px solid"
            borderColor="gray.700"
        >
            <VStack gap={1.5} align="flex-start">
                <Text fontSize="xs" fontWeight="bold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                    System Stats
                </Text>
                <VStack gap={1.5} align="stretch" w="100%">
                    <HStack justify="space-between" fontSize="xs" align="center">
                        <Text color="gray.400"  w="57px" fontWeight="medium">
                            CPU:
                        </Text>
                        <Sparkline values={cpuHistory} color="#90cdf4" />
                        <Text color="blue.300" w="40px" fontFamily="mono">
                            {(telemetry.cpu.load * 100).toFixed(1)}%
                        </Text>
                    </HStack>
                    {firstGpu && (
                        <>
                            <HStack justify="space-between" fontSize="xs" align="center">
                                <Text color="gray.400" w="57px"  fontWeight="medium">
                                    GPU:
                                </Text>
                                <Sparkline values={gpuHistory} color="#c084fc" />
                                <Text color="purple.300" w="40px" fontFamily="mono">
                                    {(firstGpu.load * 100).toFixed(1)}%
                                </Text>
                            </HStack>
                            <HStack justify="space-between" fontSize="xs" align="center">
                                <Text color="gray.400" w="57px" fontWeight="medium">
                                    GPU Mem:
                                </Text>
                                <Sparkline values={gpuMemoryHistory} color="#86efac" />
                                <Text color="green.300" w="40px"  fontFamily="mono">
                                    {gpuMemoryPercent.toFixed(1)}%
                                </Text>
                            </HStack>
                        </>
                    )}
                </VStack>
            </VStack>
        </Box>
    );
};

export default TelemetryDisplay;

