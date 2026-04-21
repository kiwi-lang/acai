import { useEffect, useRef } from 'react';
import { Box } from '@chakra-ui/react';
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
    const animationFrameRef = useRef<number | null>(null);

    useEffect(() => {
        if (!containerRef.current) return;

        const container = containerRef.current;

        // Use requestAnimationFrame to ensure container has dimensions
        const initFrame = requestAnimationFrame(() => {
            const width = container.clientWidth || 800;
            const height = container.clientHeight || 500;

            console.log('Initializing MeshViewer:', { width, height, glbUrl });

            // Create scene
            const scene = new THREE.Scene();
            scene.background = new THREE.Color(0x1a1a1a);
            sceneRef.current = scene;

            // Create camera
            const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
            camera.position.set(0, 0, 5);
            camera.lookAt(0, 0, 0);
            cameraRef.current = camera;

            // Create renderer
            const renderer = new THREE.WebGLRenderer({ antialias: true });
            renderer.setSize(width, height);
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            container.appendChild(renderer.domElement);
            rendererRef.current = renderer;

            // Add lights
            const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
            scene.add(ambientLight);

            const directionalLight1 = new THREE.DirectionalLight(0xffffff, 0.8);
            directionalLight1.position.set(5, 5, 5);
            scene.add(directionalLight1);

            const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.4);
            directionalLight2.position.set(-5, -5, -5);
            scene.add(directionalLight2);

            // Create orbit controls
            const controls = new OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.dampingFactor = 0.1;
            controls.enableRotate = true;
            controls.enableZoom = true;
            controls.enablePan = true;
            controls.rotateSpeed = 1.0;
            controls.zoomSpeed = 1.2;
            controls.panSpeed = 0.8;
            controls.minDistance = 1;
            controls.maxDistance = 100;
            controls.target.set(0, 0, 0);
            controls.update();
            controlsRef.current = controls;

            // Load GLTF model
            const loader = new GLTFLoader();
            loader.load(
                glbUrl,
                (gltf: any) => {
                    console.log('GLTF loaded:', gltf);
                    const model = gltf.scene;

                    // Calculate bounding box
                    const box = new THREE.Box3().setFromObject(model);
                    const center = box.getCenter(new THREE.Vector3());
                    const size = box.getSize(new THREE.Vector3());
                    const maxDim = Math.max(size.x, size.y, size.z);

                    console.log('Model bounds:', { center: center.toArray(), size: size.toArray(), maxDim });

                    // Scale and center model
                    if (maxDim > 0) {
                        const scale = 2 / maxDim;
                        model.scale.multiplyScalar(scale);
                        model.position.sub(center.multiplyScalar(scale));

                        // Position camera to view model
                        const distance = maxDim * 1.5;
                        camera.position.set(distance, distance, distance);
                    } else {
                        // Fallback
                        camera.position.set(5, 5, 5);
                    }

                    scene.add(model);
                    camera.lookAt(0, 0, 0);
                    controls.target.set(0, 0, 0);
                    controls.update();
                    console.log('Model added, camera at:', camera.position.toArray());
                },
                (progress: any) => {
                    if (progress.total > 0) {
                        const percent = (progress.loaded / progress.total) * 100;
                        console.log('Loading:', percent.toFixed(1) + '%');
                    }
                },
                (error: any) => {
                    console.error('Error loading GLTF:', error);
                }
            );

            // Animation loop
            const animate = () => {
                animationFrameRef.current = requestAnimationFrame(animate);
                if (controlsRef.current) {
                    controlsRef.current.update();
                }
                if (rendererRef.current && sceneRef.current && cameraRef.current) {
                    rendererRef.current.render(sceneRef.current, cameraRef.current);
                }
            };
            animate();

            // Handle window resize
            const handleResize = () => {
                if (!containerRef.current || !cameraRef.current || !rendererRef.current) return;
                const newWidth = containerRef.current.clientWidth;
                const newHeight = containerRef.current.clientHeight;
                cameraRef.current.aspect = newWidth / newHeight;
                cameraRef.current.updateProjectionMatrix();
                rendererRef.current.setSize(newWidth, newHeight);
            };
            window.addEventListener('resize', handleResize);

            // Store cleanup function
            (window as any).__meshViewerCleanup = () => {
                window.removeEventListener('resize', handleResize);
                if (animationFrameRef.current !== null) {
                    cancelAnimationFrame(animationFrameRef.current);
                }
                if (controlsRef.current) {
                    controlsRef.current.dispose();
                }
                if (rendererRef.current) {
                    rendererRef.current.dispose();
                    if (containerRef.current && rendererRef.current.domElement) {
                        containerRef.current.removeChild(rendererRef.current.domElement);
                    }
                }
                if (sceneRef.current) {
                    sceneRef.current.traverse((object: any) => {
                        if (object instanceof THREE.Mesh) {
                            if (object.geometry) object.geometry.dispose();
                            if (object.material) {
                                if (Array.isArray(object.material)) {
                                    object.material.forEach((mat: any) => mat.dispose());
                                } else {
                                    object.material.dispose();
                                }
                            }
                        }
                    });
                }
            };
        });

        // Cleanup
        return () => {
            cancelAnimationFrame(initFrame);
            if ((window as any).__meshViewerCleanup) {
                (window as any).__meshViewerCleanup();
                delete (window as any).__meshViewerCleanup;
            }
        };
    }, [glbUrl]);

    return (
        <Box
            ref={containerRef}
            w={width}
            h={height}
            borderRadius="md"
            overflow="hidden"
            bg="gray.900"
        />
    );
};

export default MeshViewer;
