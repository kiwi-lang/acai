import { useRef } from 'react';
import { IconButton, HStack, Text } from '@chakra-ui/react';

interface FileUploadProps {
    onImageUpload?: (file: File) => void;
    onAudioUpload?: (file: File) => void;
    disabled?: boolean;
}

const ImageIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
    </svg>
);

const MicIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
);

const FileUpload = ({ onImageUpload, onAudioUpload, disabled = false }: FileUploadProps) => {
    const imageInputRef = useRef<HTMLInputElement>(null);
    const audioInputRef = useRef<HTMLInputElement>(null);

    const handleImageClick = () => {
        if (!disabled && onImageUpload) {
            imageInputRef.current?.click();
        }
    };

    const handleAudioClick = () => {
        if (!disabled && onAudioUpload) {
            audioInputRef.current?.click();
        }
    };

    const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file && onImageUpload) {
            onImageUpload(file);
        }
        // Reset input
        if (imageInputRef.current) {
            imageInputRef.current.value = '';
        }
    };

    const handleAudioChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file && onAudioUpload) {
            onAudioUpload(file);
        }
        // Reset input
        if (audioInputRef.current) {
            audioInputRef.current.value = '';
        }
    };

    return (
        <HStack gap={1}>
            {onImageUpload && (
                <>
                    <input
                        ref={imageInputRef}
                        type="file"
                        accept="image/*"
                        onChange={handleImageChange}
                        style={{ display: 'none' }}
                    />
                    <IconButton
                        aria-label="Upload image"
                        onClick={handleImageClick}
                        disabled={disabled}
                        variant="ghost"
                        size="sm"
                        color="gray.600"
                        _hover={{ bg: 'gray.100' }}
                    >
                        <ImageIcon />
                    </IconButton>
                </>
            )}

            {onAudioUpload && (
                <>
                    <input
                        ref={audioInputRef}
                        type="file"
                        accept="audio/*"
                        onChange={handleAudioChange}
                        style={{ display: 'none' }}
                    />
                    <IconButton
                        aria-label="Upload audio"
                        onClick={handleAudioClick}
                        disabled={disabled}
                        variant="ghost"
                        size="sm"
                        color="gray.600"
                        _hover={{ bg: 'gray.100' }}
                    >
                        <MicIcon />
                    </IconButton>
                </>
            )}
        </HStack>
    );
};

export default FileUpload;

