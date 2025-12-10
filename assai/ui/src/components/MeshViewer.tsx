import { useEffect, useRef, useState } from 'react';
import { Box, HStack, Button, Switch, Text, VStack, Slider, SliderTrack, SliderFilledTrack, SliderThumb } from '@chakra-ui/react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

interface MeshViewerProps {
    glbUrl: string;
    width?: string;
    height?: string;
}

const MeshViewer = ({ glbUrl, width = '100%', height = '500px' }: MeshViewerProps) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const sceneRef = useRef<THREE.Scene | null>(null);
    const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
    const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
    const controlsRef = useRef<OrbitControls | null>(null);
    const modelRef = useRef<THREE.Group | null>(null);
    const initialCameraPositionRef = useRef<THREE.Vector3 | null>(null);
    const ambientLightRef = useRef<THREE.AmbientLight | null>(null);
    const directionalLight1Ref = useRef<THREE.DirectionalLight | null>(null);
    const directionalLight2Ref = useRef<THREE.DirectionalLight | null>(null);
    const gridHelperRef = useRef<THREE.GridHelper | null>(null);

    const [wireframe, setWireframe] = useState(false);
    const [showGrid, setShowGrid] = useState(false);
    const [autoRotate, setAutoRotate] = useState(false);
    const [lightIntensity, setLightIntensity] = useState(0.8);
    const [ambientIntensity, setAmbientIntensity] = useState(0.6);

    useEffect(() => {
        if (!containerRef.current) return;

        // Create scene
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x1a1a1a);
        sceneRef.current = scene;

        // Create camera
        const camera = new THREE.PerspectiveCamera(
            75,
            containerRef.current.clientWidth / containerRef.current.clientHeight,
            0.1,
            1000
        );
        camera.position.set(0, 0, 5);
        cameraRef.current = camera;
        initialCameraPositionRef.current = camera.position.clone();

        // Create renderer
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        containerRef.current.appendChild(renderer.domElement);
        rendererRef.current = renderer;

        // Add lights
        const ambientLight = new THREE.AmbientLight(0xffffff, ambientIntensity);
        scene.add(ambientLight);
        ambientLightRef.current = ambientLight;

        const directionalLight1 = new THREE.DirectionalLight(0xffffff, lightIntensity);
        directionalLight1.position.set(5, 5, 5);
        scene.add(directionalLight1);
        directionalLight1Ref.current = directionalLight1;

        const directionalLight2 = new THREE.DirectionalLight(0xffffff, lightIntensity * 0.5);
        directionalLight2.position.set(-5, -5, -5);
        scene.add(directionalLight2);
        directionalLight2Ref.current = directionalLight2;

        // Add grid helper
        const gridHelper = new THREE.GridHelper(10, 10, 0x444444, 0x222222);
        gridHelper.visible = showGrid;
        scene.add(gridHelper);
        gridHelperRef.current = gridHelper;

        // Add orbit controls
        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.autoRotate = autoRotate;
        controls.autoRotateSpeed = 2.0;
        controlsRef.current = controls;

        // Load GLTF model
        const loader = new GLTFLoader();

        // Handle data URL format
        const loadModel = () => {
            loader.load(
                glbUrl,
                (gltf) => {
                    const model = gltf.scene;

                    // Center and scale model
                    const box = new THREE.Box3().setFromObject(model);
                    const center = box.getCenter(new THREE.Vector3());
                    const size = box.getSize(new THREE.Vector3());
                    const maxDim = Math.max(size.x, size.y, size.z);
                    const scale = maxDim > 0 ? 2 / maxDim : 1;

                    model.scale.multiplyScalar(scale);
                    model.position.sub(center.multiplyScalar(scale));

                    scene.add(model);
                    modelRef.current = model;
                },
                (progress) => {
                    // Progress callback
                    if (progress.total > 0) {
                        console.log('Loading progress:', (progress.loaded / progress.total) * 100 + '%');
                    }
                },
                (error) => {
                    console.error('Error loading GLTF:', error);
                }
            );
        };

        loadModel();

        // Animation loop
        let animationId: number | null = null;
        const animate = () => {
            animationId = requestAnimationFrame(animate);
            if (controls) {
                controls.update();
            }
            renderer.render(scene, camera);
        };
        animate();

        // Handle resize
        const handleResize = () => {
            if (!containerRef.current || !camera || !renderer) return;
            camera.aspect = containerRef.current.clientWidth / containerRef.current.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight);
        };
        window.addEventListener('resize', handleResize);

        // Cleanup
        return () => {
            window.removeEventListener('resize', handleResize);
            if (animationId !== null) {
                cancelAnimationFrame(animationId);
            }
            if (containerRef.current && renderer.domElement) {
                containerRef.current.removeChild(renderer.domElement);
            }
            renderer.dispose();
            controls.dispose();
        };
    }, [glbUrl, wireframe, showGrid, autoRotate, lightIntensity, ambientIntensity]);

    // Update wireframe mode
    useEffect(() => {
        if (modelRef.current) {
            modelRef.current.traverse((child) => {
                if (child instanceof THREE.Mesh) {
                    if (child.material instanceof THREE.MeshStandardMaterial ||
                        child.material instanceof THREE.MeshPhongMaterial ||
                        child.material instanceof THREE.MeshLambertMaterial) {
                        child.material.wireframe = wireframe;
                    }
                }
            });
        }
    }, [wireframe]);

    // Update grid visibility
    useEffect(() => {
        if (gridHelperRef.current) {
            gridHelperRef.current.visible = showGrid;
        }
    }, [showGrid]);

    // Update auto-rotate
    useEffect(() => {
        if (controlsRef.current) {
            controlsRef.current.autoRotate = autoRotate;
        }
    }, [autoRotate]);

    // Update light intensities
    useEffect(() => {
        if (directionalLight1Ref.current) {
            directionalLight1Ref.current.intensity = lightIntensity;
        }
        if (directionalLight2Ref.current) {
            directionalLight2Ref.current.intensity = lightIntensity * 0.5;
        }
    }, [lightIntensity]);

    useEffect(() => {
        if (ambientLightRef.current) {
            ambientLightRef.current.intensity = ambientIntensity;
        }
    }, [ambientIntensity]);

    const resetCamera = () => {
        if (cameraRef.current && initialCameraPositionRef.current && controlsRef.current) {
            cameraRef.current.position.copy(initialCameraPositionRef.current);
            controlsRef.current.reset();
        }
    };

    return (
        <VStack gap={2} align="stretch">
            {/* Control Panel */}
            <Box
                p={3}
                bg="gray.800"
                borderRadius="md"
                border="1px solid"
                borderColor="gray.700"
            >
                <VStack gap={3} align="stretch">
                    {/* Reset Camera Button */}
                    <HStack justify="space-between">
                        <Text fontSize="sm" fontWeight="medium" color="gray.300">Controls</Text>
                        <Button
                            size="xs"
                            variant="outline"
                            onClick={resetCamera}
                            color="gray.200"
                            borderColor="gray.600"
                            _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
                        >
                            Reset Camera
                        </Button>
                    </HStack>

                    {/* Wireframe Toggle */}
                    <HStack justify="space-between">
                        <Text fontSize="sm" color="gray.400">Wireframe</Text>
                        <Switch
                            isChecked={wireframe}
                            onChange={(e) => setWireframe(e.target.checked)}
                            colorScheme="purple"
                            size="sm"
                        />
                    </HStack>

                    {/* Grid Toggle */}
                    <HStack justify="space-between">
                        <Text fontSize="sm" color="gray.400">Show Grid</Text>
                        <Switch
                            isChecked={showGrid}
                            onChange={(e) => setShowGrid(e.target.checked)}
                            colorScheme="purple"
                            size="sm"
                        />
                    </HStack>

                    {/* Auto Rotate Toggle */}
                    <HStack justify="space-between">
                        <Text fontSize="sm" color="gray.400">Auto Rotate</Text>
                        <Switch
                            isChecked={autoRotate}
                            onChange={(e) => setAutoRotate(e.target.checked)}
                            colorScheme="purple"
                            size="sm"
                        />
                    </HStack>

                    {/* Light Intensity */}
                    <VStack align="stretch" gap={1}>
                        <HStack justify="space-between">
                            <Text fontSize="sm" color="gray.400">Light Intensity</Text>
                            <Text fontSize="sm" color="gray.300">{lightIntensity.toFixed(1)}</Text>
                        </HStack>
                        <Slider
                            value={lightIntensity}
                            onChange={(val) => setLightIntensity(val)}
                            min={0}
                            max={2}
                            step={0.1}
                            colorScheme="purple"
                            size="sm"
                        >
                            <SliderTrack>
                                <SliderFilledTrack />
                            </SliderTrack>
                            <SliderThumb />
                        </Slider>
                    </VStack>

                    {/* Ambient Light Intensity */}
                    <VStack align="stretch" gap={1}>
                        <HStack justify="space-between">
                            <Text fontSize="sm" color="gray.400">Ambient Light</Text>
                            <Text fontSize="sm" color="gray.300">{ambientIntensity.toFixed(1)}</Text>
                        </HStack>
                        <Slider
                            value={ambientIntensity}
                            onChange={(val) => setAmbientIntensity(val)}
                            min={0}
                            max={1}
                            step={0.1}
                            colorScheme="purple"
                            size="sm"
                        >
                            <SliderTrack>
                                <SliderFilledTrack />
                            </SliderTrack>
                            <SliderThumb />
                        </Slider>
                    </VStack>
                </VStack>
            </Box>

            {/* 3D Viewer */}
            <Box
                ref={containerRef}
                w={width}
                h={height}
                borderRadius="md"
                overflow="hidden"
                bg="gray.900"
            />
        </VStack>
    );
};

export default MeshViewer;

