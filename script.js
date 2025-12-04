// --- Page Loader ---
window.addEventListener('load', () => {
    const loader = document.getElementById('page-loader');
    if (loader) {
        loader.style.opacity = '0';
        setTimeout(() => {
            loader.remove();
        }, 500);
    }
});

// --- Back to Top Script ---
const backToTop = document.getElementById('back-to-top');
if (backToTop) {
    window.addEventListener('scroll', () => {
        if (window.scrollY > 500) {
            backToTop.style.opacity = '1';
            backToTop.style.pointerEvents = 'auto';
        } else {
            backToTop.style.opacity = '0';
            backToTop.style.pointerEvents = 'none';
        }
    });
    backToTop.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
}

// --- Konami Code Script (Item 16) ---
let konamiCode = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a'];
let konamiIndex = 0;
document.addEventListener('keydown', (e) => {
    if (e.key === konamiCode[konamiIndex]) {
        konamiIndex++;
        if (konamiIndex === konamiCode.length) {
            // Activate rainbow mode!
            document.body.style.animation = 'rainbow 5s linear infinite';
            konamiIndex = 0;
            setTimeout(() => {
                document.body.style.animation = '';
            }, 5000);
        }
    } else {
        konamiIndex = 0;
    }
});

// --- Main Module Script ---

// --- NEW: Performance Consts (Item 6) ---
const isMobile = window.matchMedia('(max-width: 768px)').matches;
const isLowPerformance = navigator.hardwareConcurrency < 4;

// --- DOM Elements ---
const slider = document.getElementById('angle-slider');
const sliderWrapper = document.querySelector('.slider-wrapper');
const horn = document.getElementById('servo-horn-group');
const gears = [
    document.getElementById('gear1'),
    document.getElementById('gear2'),
    document.getElementById('gear3')
];
const motorEls = document.querySelectorAll('.servo-motor');

const angleReadout = document.getElementById('angle-value');
const pulseReadout = document.getElementById('pulse-value');
const dutyReadout = document.getElementById('duty-value');

const pwmCanvas = document.getElementById('pwm-canvas');
const pwmCtx = pwmCanvas ? pwmCanvas.getContext('2d') : null;

const btn0 = document.getElementById('btn-0');
const btn90 = document.getElementById('btn-90');
const btn180 = document.getElementById('btn-180');
const btnSweep = document.getElementById('btn-sweep');
const copyCodeBtn = document.getElementById('copy-code');

const svgContainer = document.getElementById('svg-servo-container');
const svg = document.getElementById('servo-diagram-svg');
const particlesGroup = document.getElementById('particles-group');
const speedLines = document.getElementById('speed-lines');
const angleArc = document.getElementById('angle-arc');
const angleArcText = document.getElementById('angle-arc-text');

const hero = document.getElementById('hero');
const heroInner = document.querySelector('.hero-inner');
const heroConstellation = document.getElementById('hero-constellation');
const scrollIndicator = document.querySelector('.scroll-indicator');
const heroTitleEl = document.getElementById('hero-title');

const heroVisualCanvas = document.getElementById('hero-3d-visual');
const logo = document.querySelector('.logo');
// const spotlight = document.getElementById('cursor-spotlight'); // REMOVED
// const trailCanvas = document.getElementById('cursor-trail'); // REMOVED
// const trailCtx = trailCanvas ? trailCanvas.getContext('2d') : null; // REMOVED
// let trail = []; // REMOVED

const blobCanvas = document.getElementById('background-blobs');
const blobCtx = blobCanvas ? blobCanvas.getContext('2d') : null;
let blobs = [];

// REMOVED: shareBtn
// const themeToggle = document.getElementById('theme-toggle'); // REMOVED

// NEW: Chart.js
const chartCanvas = document.getElementById('performance-chart');

// NEW: Code Preview
const runCodeBtn = document.getElementById('run-code-btn');
const codeAnswer = document.getElementById('code-answer');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;

// NEW: Learning Progress
const progressBarLabel = document.getElementById('progress-bar-label');
const progressBarInner = document.getElementById('progress-bar-inner');
const achievementToast = document.getElementById('achievement-toast');
const QUIZ_COUNT = 5; // 4 questions + 1 coding challenge
let learningProgress = {
    completedQuizzes: new Set(),
    achievements: new Set()
};

// NEW: Sound Effects
let audioCtx;
let mainGain;
try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    mainGain = audioCtx.createGain();
    mainGain.gain.value = 0.3; // Start at 30% volume
    mainGain.connect(audioCtx.destination);
} catch (e) {
    console.warn("Web Audio API not supported. Sound effects will be disabled.", e);
}

// NEW: Presentation Mode
let presentationMode = false;

// NEW: Annotation
const annotationCanvas = document.getElementById('annotation-canvas');
const annotationCtx = annotationCanvas.getContext('2d');
let isDrawing = false;
let drawingMode = false;
let drawings = [];

// NEW: Laser Pointer
const laserSpotlight = document.getElementById('laser-spotlight');
let spotlightActive = false;

// NEW: Performance Monitor
const perfMonitor = document.getElementById('perf-monitor');
let frameCount = 0;
let lastTime = performance.now();
let fps = 60;
let perfMonitorVisible = false;

// NEW: Auto-Pilot
const demoSequences = {
    'basic': [
        { angle: 0, duration: 1000, label: 'Setting to 0°' },
        { angle: 90, duration: 1000, label: 'Setting to 90°' },
        { angle: 180, duration: 1000, label: 'Setting to 180°' },
        { angle: 90, duration: 500, label: 'Return to Center' }
    ],
    'sweep': [
        { sweep: true, duration: 5000, label: 'Starting Auto-Sweep' },
        { angle: 90, duration: 500, label: 'Stopping at 90°' }
    ],
    'precise': [
        { angle: 45, duration: 800, label: 'Precise Move: 45°' },
        { angle: 135, duration: 800, label: 'Precise Move: 135°' },
        { angle: 90, duration: 800, label: 'Precise Move: 90°' }
    ]
};
let currentSequence = null;
let sequenceIndex = 0;
let sequenceTimeout = null;

// NEW: Recording
let mediaStream = null;

// NEW: Shortcuts Overlay
const shortcuts = {
    'Ctrl + P': 'Toggle Presentation Mode',
    'Ctrl + D': 'Toggle Drawing Mode',
    'Ctrl + L': 'Toggle Laser Pointer',
    'Ctrl + Shift + P': 'Toggle Performance Monitor',
    'Ctrl + Shift + C': 'Clear Annotations',
    '?': 'Show This Help Overlay',
    'Esc': 'Close Overlay / Exit Presentation',
    '0': 'Set Servo to 0°',
    '9': 'Set Servo to 90°',
    '8': 'Set Servo to 180°',
    'S': 'Toggle Auto-Sweep',
    '← / →': 'Fine Control (±5°)',
};

let confetti = [];
const confettiColors = ['#007AFF', '#4A9FDB', '#f39c12', '#f3b140', '#FFFFFF'];

// --- Gear Positions ---
const gearTransforms = [
    { x: 125, y: 125, scale: 0.8 },
    { x: 145, y: 105, scale: 1.2 },
    { x: 150, y: 70,  scale: 1.0 }
];

// --- Animation Loop State ---
let targetAngle = 90;
let currentAngle = 90;
let lastDisplayedAngle = 90;
let autoSweep = false;
let sweepDirection = 1;

// --- NEW: Sound Functions ---
function playSound(type, freq, duration, gain = 0.3) {
    if (!audioCtx) return;
    // Resume context if it's suspended (e.g., autoplay policy)
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    
    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(mainGain);
    
    oscillator.frequency.value = freq;
    oscillator.type = type;
    
    gainNode.gain.setValueAtTime(gain, audioCtx.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
    
    oscillator.start(audioCtx.currentTime);
    oscillator.stop(audioCtx.currentTime + duration);
}

function playClick() {
    playSound('sine', 800, 0.1, 0.2);
}
function playSuccess() {
    playSound('sine', 600, 0.1);
    setTimeout(() => playSound('sine', 1200, 0.2), 100);
}
function playError() {
    playSound('square', 200, 0.2);
}

let lastServoSoundTime = 0;
function playServoSound(speed) {
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    // Throttle sound to prevent buzzing
    if (now - lastServoSoundTime < 0.1) return;
    lastServoSoundTime = now;
    
    // Louder/higher pitch for faster speed
    const freq = 100 + Math.min(speed, 20) * 5;
    const duration = 0.2;
    playSound('sawtooth', freq, duration, 0.1); // Quieter sound
}

// --- NEW: Learning Progress Functions ---
function loadProgress() {
    try {
        const saved = localStorage.getItem('servolab_progress');
        if (saved) {
            const parsed = JSON.parse(saved);
            learningProgress.completedQuizzes = new Set(parsed.completedQuizzes);
            learningProgress.achievements = new Set(parsed.achievements);
        }
    } catch (e) {
        console.warn('Could not load progress from localStorage', e);
    }
    updateProgressBar();
}

function saveProgress() {
    try {
        localStorage.setItem('servolab_progress', JSON.stringify({
            completedQuizzes: [...learningProgress.completedQuizzes],
            achievements: [...learningProgress.achievements]
        }));
    } catch (e) {
        console.warn('Could not save progress to localStorage', e);
    }
}

function updateProgress(quizId) {
    if (learningProgress.completedQuizzes.has(quizId)) return; // Don't re-add
    
    learningProgress.completedQuizzes.add(quizId);
    saveProgress();
    updateProgressBar();
    
    if (learningProgress.completedQuizzes.size >= QUIZ_COUNT) {
        unlockAchievement('quiz_master', '🏆', 'Quiz Master', 'You completed all exercises!');
    }
}

function updateProgressBar() {
    const count = learningProgress.completedQuizzes.size;
    const percent = (count / QUIZ_COUNT) * 100;
    
    if (progressBarLabel) progressBarLabel.textContent = `Learning Progress: ${count}/${QUIZ_COUNT}`;
    if (progressBarInner) progressBarInner.style.width = `${percent}%`;
}

function unlockAchievement(id, icon, title, desc) {
    if (learningProgress.achievements.has(id)) return; // Already unlocked
    
    learningProgress.achievements.add(id);
    saveProgress();
    showAchievement(icon, title, desc);
}

function showAchievement(icon, title, desc) {
    if (!achievementToast) return;
    
    document.getElementById('achievement-icon').textContent = icon;
    document.getElementById('achievement-title').textContent = title;
    document.getElementById('achievement-desc').textContent = desc;
    
    achievementToast.classList.add('show');
    playSuccess(); // Achievement sound
    
    setTimeout(() => {
        achievementToast.classList.remove('show');
    }, 4000);
}

// --- NEW: Notification Toast ---
function showNotification(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `notification-toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    
    // Trigger show animation
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);
    
    // Hide and remove
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            toast.remove();
        }, 500);
    }, 3000);
}

// --- NEW: Presentation Mode ---
function togglePresentationMode() {
    presentationMode = !presentationMode;
    playClick();

    if (presentationMode) {
        document.body.classList.add('presentation-mode');
        document.documentElement.requestFullscreen().catch(err => {
            console.warn(`Fullscreen request failed: ${err.message}`);
        });
        showPresenterControls();
    } else {
        document.body.classList.remove('presentation-mode');
        if (document.fullscreenElement) {
            document.exitFullscreen();
        }
        const controls = document.getElementById('presenter-controls');
        if (controls) controls.remove();
        
        // Ensure all presentation tools are off
        if (drawingMode) toggleDrawingMode();
        if (spotlightActive) toggleSpotlight();
        if (perfMonitorVisible) togglePerfMonitor();
        if (currentSequence) stopAutoPilot();
        if (mediaStream) stopRecording();
    }
}

function showPresenterControls() {
    const controls = document.createElement('div');
    controls.id = 'presenter-controls';
    controls.className = 'presenter-panel';
    controls.innerHTML = `
        <button id="btn-present-0" data-tooltip="Set to 0°">0°</button>
        <button id="btn-present-45" data-tooltip="Set to 45°">45°</button>
        <button id="btn-present-90" data-tooltip="Set to 90°">90°</button>
        <button id="btn-present-135" data-tooltip="Set to 135°">135°</button>
        <button id="btn-present-180" data-tooltip="Set to 180°">180°</button>
        <button id="btn-present-sweep" data-tooltip="Toggle Auto-Sweep (S)">Sweep</button>
        
        <div class="button-group">
            <button id="btn-present-basic" data-tooltip="Run Basic Demo">Demo: Basic</button>
            <button id="btn-present-sweep-demo" data-tooltip="Run Sweep Demo">Demo: Sweep</button>
            <button id="btn-present-precise" data-tooltip="Run Precise Demo">Demo: Precise</button>
        </div>
        
        <div class="live-metrics">
            <div class="metric-large">
                <span id="present-auto-angle">90.0°</span>
                <p>Angle</p>
            </div>
            <div class="metric-large">
                <span id="present-auto-pulse">1.50 ms</span>
                <p>Pulse</p>
            </div>
        </div>
        
        <div class="button-group">
            <button id="btn-present-draw" data-tooltip="Toggle Drawing (Ctrl+D)">Draw</button>
            <button id="btn-present-laser" data-tooltip="Toggle Laser (Ctrl+L)">Laser</button>
            <button id="btn-present-perf" data-tooltip="Toggle Stats (Ctrl+Shift+P)">Stats</button>
            <button id="btn-present-rec" data-tooltip="Start Recording">Rec</button>
            <button id="btn-present-shortcuts" data-tooltip="Show Shortcuts (?)">Help</button>
            <button id="btn-present-exit" class="danger">Exit (Esc)</button>
        </div>
    `;
    document.body.appendChild(controls);
    
    // Add listeners
    document.getElementById('btn-present-0').addEventListener('click', () => setAngle(0));
    document.getElementById('btn-present-45').addEventListener('click', () => setAngle(45));
    document.getElementById('btn-present-90').addEventListener('click', () => setAngle(90));
    document.getElementById('btn-present-135').addEventListener('click', () => setAngle(135));
    document.getElementById('btn-present-180').addEventListener('click', () => setAngle(180));
    document.getElementById('btn-present-sweep').addEventListener('click', toggleSweep);
    document.getElementById('btn-present-exit').addEventListener('click', togglePresentationMode);
    
    document.getElementById('btn-present-draw').addEventListener('click', toggleDrawingMode);
    document.getElementById('btn-present-laser').addEventListener('click', toggleSpotlight);
    document.getElementById('btn-present-perf').addEventListener('click', togglePerfMonitor);
    document.getElementById('btn-present-rec').addEventListener('click', startRecording);
    document.getElementById('btn-present-shortcuts').addEventListener('click', showShortcutsHelp);
    
    document.getElementById('btn-present-basic').addEventListener('click', () => startAutoPilot('basic'));
    document.getElementById('btn-present-sweep-demo').addEventListener('click', () => startAutoPilot('sweep'));
    document.getElementById('btn-present-precise').addEventListener('click', () => startAutoPilot('precise'));
    
    // Sync sweep button
    if (autoSweep) {
        document.getElementById('btn-present-sweep').classList.add('active');
    }
}

// --- NEW: Annotation ---
function toggleDrawingMode() {
    playClick();
    drawingMode = !drawingMode;
    annotationCanvas.style.pointerEvents = drawingMode ? 'auto' : 'none';
    document.body.style.cursor = drawingMode ? 'crosshair' : 'none';
    
    const btn = document.getElementById('btn-present-draw');
    if (btn) btn.classList.toggle('active', drawingMode);
    
    if (drawingMode && spotlightActive) toggleSpotlight(); // Turn off laser
}

function clearAnnotations() {
    drawings.length = 0;
    annotationCtx.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
}

// --- NEW: Laser Pointer ---
function toggleSpotlight() {
    playClick();
    spotlightActive = !spotlightActive;
    laserSpotlight.style.display = spotlightActive ? 'block' : 'none';
    document.body.style.cursor = spotlightActive ? 'none' : 'default'; // Use default, not 'none'
    
    const btn = document.getElementById('btn-present-laser');
    if (btn) btn.classList.toggle('active', spotlightActive);
    
    if (spotlightActive && drawingMode) toggleDrawingMode(); // Turn off drawing
}

// --- NEW: Performance Monitor ---
function togglePerfMonitor() {
    playClick();
    perfMonitorVisible = !perfMonitorVisible;
    perfMonitor.classList.toggle('visible', perfMonitorVisible);
    
    const btn = document.getElementById('btn-present-perf');
    if (btn) btn.classList.toggle('active', perfMonitorVisible);
    
    if (perfMonitorVisible) {
        lastTime = performance.now();
        frameCount = 0;
        updatePerfMonitor(); // Start the loop
    }
}

function updatePerfMonitor() {
    if (!perfMonitorVisible) return;
    
    frameCount++;
    const now = performance.now();
    const delta = now - lastTime;
    
    if (delta >= 1000) {
        fps = Math.round((frameCount * 1000) / delta);
        frameCount = 0;
        lastTime = now;
    }
    
    const memory = performance.memory ? (performance.memory.usedJSHeapSize / 1048576).toFixed(2) : 'N/A';
    
    perfMonitor.innerHTML = `
        <div><strong>FPS:</strong> ${fps}</div>
        <div><strong>Memory:</strong> ${memory} MB</div>
        <div><strong>Angle:</strong> ${currentAngle.toFixed(1)}°</div>
        <div><strong>Target:</strong> ${targetAngle.toFixed(1)}°</div>
    `;
    
    requestAnimationFrame(updatePerfMonitor);
}

// --- NEW: Auto-Pilot ---
function startAutoPilot(sequenceName) {
    playClick();
    if (currentSequence) stopAutoPilot(); // Stop previous one
    
    currentSequence = demoSequences[sequenceName];
    sequenceIndex = 0;
    
    showSequenceLabel();
    runNextStep();
}

function runNextStep() {
    if (sequenceIndex >= currentSequence.length) {
        stopAutoPilot();
        return;
    }
    
    const step = currentSequence[sequenceIndex];
    
    // Show label
    const labelEl = document.getElementById('sequence-label');
    if(labelEl) labelEl.textContent = step.label;
    
    // Execute action
    if (step.sweep) {
        startSweep();
    } else {
        stopSweep();
        targetAngle = step.angle;
    }
    
    // Schedule next step
    sequenceTimeout = setTimeout(() => {
        sequenceIndex++;
        runNextStep();
    }, step.duration);
}

function stopAutoPilot() {
    clearTimeout(sequenceTimeout);
    currentSequence = null;
    stopSweep();
    
    const labelEl = document.getElementById('sequence-label');
    if (labelEl) {
        labelEl.textContent = 'Demo Complete';
        setTimeout(() => {
            hideSequenceLabel();
        }, 1500);
    }
}

function showSequenceLabel() {
    let label = document.getElementById('sequence-label');
    if (!label) {
        label = document.createElement('div');
        label.id = 'sequence-label';
        document.body.appendChild(label);
    }
}

function hideSequenceLabel() {
    const label = document.getElementById('sequence-label');
    if (label) {
        label.style.animation = 'fadeOutScale 0.3s ease-out forwards';
        setTimeout(() => label.remove(), 300);
    }
}

// --- NEW: Recording ---
async function startRecording() {
    playClick();
    const btn = document.getElementById('btn-present-rec');
    if (mediaStream) {
        stopRecording();
        return;
    }
    
    try {
        mediaStream = await navigator.mediaDevices.getDisplayMedia({
            video: true,
            audio: true
        });
        
        btn.textContent = 'Stop Rec';
        btn.classList.add('danger');
        
        showRecordingIndicator();
        
        mediaStream.getVideoTracks()[0].addEventListener('ended', () => {
            stopRecording();
        });
    } catch (err) {
        console.error('Recording error:', err);
        showNotification('Could not start recording.', 'error');
    }
}

function stopRecording() {
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }
    
    const btn = document.getElementById('btn-present-rec');
    if (btn) {
        btn.textContent = 'Rec';
        btn.classList.remove('danger');
    }
    
    const indicator = document.getElementById('recording-indicator');
    if (indicator) indicator.remove();
}

function showRecordingIndicator() {
    const indicator = document.createElement('div');
    indicator.id = 'recording-indicator';
    indicator.innerHTML = `<div class="rec-dot"></div><span>REC</span>`;
    document.body.appendChild(indicator);
}


// --- NEW: Shortcuts Overlay ---
function showShortcutsHelp() {
    playClick();
    let overlay = document.getElementById('shortcuts-overlay');
    if (overlay && overlay.classList.contains('visible')) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 300);
        return;
    }
    
    overlay = document.createElement('div');
    overlay.id = 'shortcuts-overlay';
    
    const panel = document.createElement('div');
    panel.className = 'panel';
    
    let html = '<h2>⌨️ Keyboard Shortcuts</h2><div class="shortcuts-grid">';
    
    for (const [key, desc] of Object.entries(shortcuts)) {
        html += `
            <div class="shortcut-item">
                <span class="shortcut-key">${key}</span>
                <span class="shortcut-desc">${desc}</span>
            </div>
        `;
    }
    
    html += '</div><button class="close-btn">Close (Esc)</button>';
    
    panel.innerHTML = html;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    
    // Show with transition
    setTimeout(() => overlay.classList.add('visible'), 10);
    
    // Add close listeners
    overlay.querySelector('.close-btn').addEventListener('click', () => {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 300);
    });
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.classList.remove('visible');
            setTimeout(() => overlay.remove(), 300);
        }
    });
}
    
    function safeInit(fn, name) {
        try {
            fn();
        } catch (error) {
            console.error(`Failed to initialize ${name}:`, error);
        }
    }

    function triggerHaptic() {
        if (navigator.vibrate) {
            navigator.vibrate(10);
        }
    }

    // --- Main Update Function ---
    function updateVisuals(angleDeg) {
        if (slider && Math.abs(parseFloat(slider.value) - angleDeg) > 0.1) {
            slider.value = angleDeg;
        }
        updateReadouts(angleDeg);
        if (pwmCtx) {
            drawPWM(angleDeg);
        }
        
        const rotation = angleDeg - 90;
        if (horn) {
            horn.style.transform = `rotate(${rotation}deg)`;
            const hue = (angleDeg / 180) * 60;
            horn.style.filter = `hue-rotate(${hue}deg) drop-shadow(0 2px 4px rgba(0,0,0,0.2))`;
        }
        
        gears.forEach((gear, index) => {
            if (gear) {
                const gearRotation = angleDeg * (index + 1) * (index % 2 === 0 ? 1 : -1.5);
                const t = gearTransforms[index];
                gear.setAttribute('transform', 
                    `translate(${t.x} ${t.y}) scale(${t.scale}) rotate(${gearRotation})`
                );
            }
        });
        
        drawAngleArc(angleDeg);
    }

    function animateValue(element, start, end, duration, decimals, suffix) {
        if (!element) return;
        if (Math.abs(start - end) < 0.1) {
            element.textContent = end.toFixed(decimals) + suffix;
            return;
        }

        const range = end - start;
        const startTime = performance.now();
        
        function update(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const value = start + (range * progress);
            
            element.textContent = value.toFixed(decimals) + suffix;
            
            if (progress < 1) {
                requestAnimationFrame(update);
            } else {
                lastDisplayedAngle = end;
                if (slider && element.id === 'angle-value') {
                    slider.setAttribute('aria-valuenow', end.toFixed(1));
                    slider.setAttribute('aria-valuetext', `${end.toFixed(1)} degrees`);
                }
            }
        }
        requestAnimationFrame(update);
    }
    
    function updateReadouts(angleDeg) {
        animateValue(angleReadout, lastDisplayedAngle, angleDeg, 150, 1, '°');
        
        const pulseWidth = 1 + (angleDeg / 180);
        if (pulseReadout) {
            pulseReadout.textContent = pulseWidth.toFixed(2) + ' ms';
        }
        const dutyCycle = (pulseWidth / 20) * 100;
        if (dutyReadout) {
            dutyReadout.textContent = dutyCycle.toFixed(1) + '%';
        }
        
        // NEW: Update presenter controls if they exist
        if (presentationMode) {
            const presentAngle = document.getElementById('present-auto-angle');
            const presentPulse = document.getElementById('present-auto-pulse');
            if (presentAngle) presentAngle.textContent = angleDeg.toFixed(1) + '°';
            if (presentPulse) presentPulse.textContent = pulseWidth.toFixed(2) + ' ms';
        }
    }

    function drawPWM(angleDeg) {
        if (!pwmCtx || !pwmCanvas) return;
        const dpr = window.devicePixelRatio || 1;
        const rect = pwmCanvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return; 
        
        pwmCanvas.width = rect.width * dpr;
        pwmCanvas.height = rect.height * dpr;
        pwmCtx.scale(dpr, dpr);
        
        const w = rect.width;
        const h = rect.height;

        pwmCtx.clearRect(0, 0, w, h);
        
        const totalTime = 20;
        const pulseWidth = 1 + (angleDeg / 180);
        
        const padding = 20;
        const graphWidth = w - 2 * padding;
        const graphHeight = h - 2 * padding;
        const yLow = padding + graphHeight * 0.8;
        const yHigh = padding + graphHeight * 0.2;
        
        const cycleWidthPx = graphWidth;
        const pulseWidthPx = (pulseWidth / totalTime) * cycleWidthPx;

        const computedStyle = getComputedStyle(document.body);
        const lineStyle = computedStyle.getPropertyValue('--accent-blue');
        const textStyle = computedStyle.getPropertyValue('--text-secondary');

        pwmCtx.strokeStyle = lineStyle;
        pwmCtx.lineWidth = 3;
        pwmCtx.lineJoin = 'round';
        pwmCtx.lineCap = 'round';

        pwmCtx.beginPath();
        pwmCtx.moveTo(padding, yLow); 
        pwmCtx.lineTo(padding, yHigh); 
        pwmCtx.lineTo(padding + pulseWidthPx, yHigh);
        pwmCtx.lineTo(padding + pulseWidthPx, yLow);
        pwmCtx.lineTo(padding + cycleWidthPx, yLow); 
        pwmCtx.stroke();

        pwmCtx.fillStyle = textStyle;
        pwmCtx.font = "600 12px 'Space Grotesk', sans-serif";
        pwmCtx.textAlign = 'center';
        
        pwmCtx.fillText('0ms', padding, yLow + 18);
        pwmCtx.fillText('20ms', padding + cycleWidthPx, yLow + 18);
        
        pwmCtx.fillStyle = lineStyle;
        pwmCtx.fillText(pulseWidth.toFixed(1) + 'ms', padding + pulseWidthPx / 2, yHigh - 8);
    }
    
    function drawAngleArc(angleDeg) {
        if (!angleArc || !angleArcText) return;
        
        const startAngle = -90;
        const endAngle = startAngle + angleDeg;
        const radius = 30;
        const centerX = 150;
        const centerY = 50;
        
        const startRad = (startAngle * Math.PI) / 180;
        const endRad = (endAngle * Math.PI) / 180;
        
        const x1 = centerX + radius * Math.cos(startRad);
        const y1 = centerY + radius * Math.sin(startRad);
        const x2 = centerX + radius * Math.cos(endRad);
        const y2 = centerY + radius * Math.sin(endRad);
        
        const largeArc = angleDeg > 180 ? 1 : 0;
        
        const pathData = `M ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`;
        angleArc.setAttribute('d', pathData);
        
        angleArcText.textContent = angleDeg.toFixed(0) + '°';
        const textAngle = startAngle + angleDeg / 2;
        const textRad = (textAngle * Math.PI) / 180;
        const textRadius = radius + 15;
        angleArcText.setAttribute('x', (centerX + textRadius * Math.cos(textRad)).toString());
        angleArcText.setAttribute('y', (centerY + textRadius * Math.sin(textRad) + 4).toString());
    }
    
    function createParticle() {
        if (!particlesGroup) return;
        const particle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        particle.setAttribute('class', 'particle');
        particle.setAttribute('r', (Math.random() * 2 + 1).toString());
        particle.setAttribute('cx', (150 + (Math.random() - 0.5) * 20).toString());
        particle.setAttribute('cy', (50 + (Math.random() - 0.5) * 20).toString());
        particle.setAttribute('fill', 'var(--accent-blue)');
        particle.setAttribute('opacity', '0.6');
        
        particlesGroup.appendChild(particle);
        
        const animation = particle.animate([
            { transform: 'translate(0, 0) scale(1)', opacity: 0.6 },
            { transform: `translate(${(Math.random() - 0.5) * 40}px, ${Math.random() * -30}px) scale(0)`, opacity: 0 }
        ], {
            duration: 1000,
            easing: 'ease-out'
        });
        
        animation.onfinish = () => particle.remove();
    }

    // --- Refactored Animation Loop ---
    function smoothLoop() {
        if (autoSweep) {
            let newAngle = currentAngle + (0.5 * sweepDirection);
            if (newAngle >= 180) { newAngle = 180; sweepDirection = -1; }
            if (newAngle <= 0) { newAngle = 0; sweepDirection = 1; }
            targetAngle = newAngle;
        }

        const speed = Math.abs(currentAngle - targetAngle);
        if (speed < 0.1) {
            currentAngle = targetAngle;
        } else {
            currentAngle += (targetAngle - currentAngle) * 0.15;
        }
        
        updateVisuals(currentAngle);
        
        const isMoving = speed > 0.5;
        motorEls.forEach(m => m.classList.toggle('active', isMoving));
        
        if (speedLines) {
            speedLines.setAttribute('opacity', Math.min(speed / 10, 0.8).toString());
        }
        
        if (isMoving) {
            if (Math.random() < 0.3) createParticle();
            // NEW: Play servo sound
            if (Math.random() < 0.1) playServoSound(speed);
        }
        
        requestAnimationFrame(smoothLoop);
    }
    
    function startSweep() {
        autoSweep = true;
        if (btnSweep) {
            btnSweep.classList.add('active');
            btnSweep.textContent = 'Stop Sweep';
            btnSweep.setAttribute('aria-label', 'Stop auto-sweep');
        }
        const presentSweep = document.getElementById('btn-present-sweep');
        if(presentSweep) presentSweep.classList.add('active');
    }

    function stopSweep() {
        autoSweep = false;
        if (btnSweep) {
            btnSweep.classList.remove('active');
            btnSweep.textContent = 'Auto-Sweep';
            btnSweep.setAttribute('aria-label', 'Toggle auto-sweep');
        }
        const presentSweep = document.getElementById('btn-present-sweep');
        if(presentSweep) presentSweep.classList.remove('active');
    }
    
    // --- NEW: Presentation Mode Helper ---
    function setAngle(angle) {
        playClick();
        stopSweep();
        targetAngle = angle;
        lastDisplayedAngle = currentAngle;
    }
    
    function toggleSweep() {
        playClick();
        if (autoSweep) stopSweep();
        else startSweep();
    }

    // --- Event Listeners ---
    if (slider) {
        slider.addEventListener('input', (e) => {
            playClick();
            stopSweep();
            targetAngle = parseFloat(e.target.value);
            lastDisplayedAngle = currentAngle;
            slider.style.transform = 'scaleY(1.1)';
            setTimeout(() => {
                slider.style.transform = 'scaleY(1)';
            }, 100);
        });
        
        slider.addEventListener('click', (e) => {
            const ripple = document.createElement('span');
            ripple.className = 'slider-ripple';
            ripple.style.left = (e.offsetX / slider.offsetWidth) * 100 + '%';
            sliderWrapper.appendChild(ripple);
            
            setTimeout(() => ripple.remove(), 600);
        });
    }

    if (btn0) btn0.addEventListener('click', () => setAngle(0));
    if (btn90) btn90.addEventListener('click', () => setAngle(90));
    if (btn180) btn180.addEventListener('click', () => setAngle(180));
    if (btnSweep) btnSweep.addEventListener('click', toggleSweep);
    
    window.addEventListener('resize', () => {
        if(pwmCtx) {
            drawPWM(currentAngle);
        }
        if(window.threejs) {
            window.threejs.camera.aspect = heroVisualCanvas.clientWidth / heroVisualCanvas.clientHeight;
            window.threejs.camera.updateProjectionMatrix();
            window.threejs.renderer.setSize(heroVisualCanvas.clientWidth, heroVisualCanvas.clientHeight);
            window.threejs.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        }
        if (blobCtx) {
            initBlobs();
        }
        if (heroConstellation) {
            initConstellation();
        }
        if (annotationCanvas) {
            annotationCanvas.width = window.innerWidth * window.devicePixelRatio;
            annotationCanvas.height = window.innerHeight * window.devicePixelRatio;
            annotationCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
            redrawAnnotations();
        }
    });
    
    function initScrollAnimations() {
        const animatedElements = document.querySelectorAll('.scroll-animate');
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                }
            });
        }, { threshold: 0.1 });

        animatedElements.forEach(el => {
            observer.observe(el);
        });
    }
    
    if (copyCodeBtn) {
        copyCodeBtn.addEventListener('click', () => {
            triggerHaptic();
            playClick();
            const code = document.getElementById('code-block').innerText;
            const textArea = document.createElement('textarea');
            textArea.value = code;
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
                copyCodeBtn.textContent = 'Copied!';
            } catch (err) {
                console.error('Failed to copy text: ', err);
                copyCodeBtn.textContent = 'Error';
            }
            document.body.removeChild(textArea);
            
            setTimeout(() => {
                copyCodeBtn.textContent = 'Copy';
            }, 2000);
        });
    }
    
    window.addEventListener('scroll', () => {
        if (presentationMode) return; // Don't run parallax in presentation mode
        const scrollTop = window.scrollY;
        const heroHeight = hero.offsetHeight;
        const scrollFraction = scrollTop / (heroHeight || 1);
        
        if (scrollFraction <= 1) {
            const opacity = Math.max(0, 1 - scrollFraction * 2);
            const scale = Math.max(0.9, 1 - scrollFraction * 0.1);
            heroInner.style.opacity = opacity;
            heroInner.style.transform = `scale(${scale})`;
            scrollIndicator.style.opacity = Math.max(0, 0.7 - scrollFraction * 2);
        }
    });

    function initConstellation() {
        if (!heroConstellation) return;
        const ctx = heroConstellation.getContext('2d');
        if (!ctx) return;
        
        const dpr = window.devicePixelRatio || 1;
        let w = heroConstellation.width = hero.clientWidth * dpr;
        let h = heroConstellation.height = hero.clientHeight * dpr;
        ctx.scale(dpr, dpr);
        w = hero.clientWidth;
        h = hero.clientHeight;

        let dots = [];
        const dotCount = isMobile ? 25 : 50; // Fewer dots on mobile
        const maxDist = 200;

        for (let i = 0; i < dotCount; i++) {
            dots.push({
                x: Math.random() * w,
                y: Math.random() * h,
                vx: (Math.random() - 0.5) * 0.3,
                vy: (Math.random() - 0.5) * 0.3
            });
        }

        function animateConstellation() {
            if (heroConstellation.width !== hero.clientWidth * dpr || heroConstellation.height !== hero.clientHeight * dpr) {
                w = heroConstellation.width = hero.clientWidth * dpr;
                h = heroConstellation.height = hero.clientHeight * dpr;
                ctx.scale(dpr, dpr);
                w = hero.clientWidth;
                h = hero.clientHeight;
            }

            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
            
            dots.forEach(dot => {
                dot.x += dot.vx;
                dot.y += dot.vy;

                if (dot.x < 0 || dot.x > w) dot.vx *= -1;
                if (dot.y < 0 || dot.y > h) dot.vy *= -1;

                ctx.beginPath();
                ctx.arc(dot.x, dot.y, 2, 0, Math.PI * 2);
                ctx.fill();
            });

            for (let i = 0; i < dotCount; i++) {
                for (let j = i + 1; j < dotCount; j++) {
                    const dist = Math.hypot(dots[i].x - dots[j].x, dots[i].y - dots[j].y);
                    if (dist < maxDist) {
                        ctx.beginPath();
                        ctx.moveTo(dots[i].x, dots[i].y);
                        ctx.lineTo(dots[j].x, dots[j].y);
                        ctx.stroke();
                    }
                }
            }
            requestAnimationFrame(animateConstellation);
        }
        animateConstellation();
    }
    
    function initLogoScramble() {
        if (!logo) return;
        
        const originalText = "ServoLab";
        logo.dataset.value = originalText;
        const scrambleChars = "<>-_\\/[]{}—=+*^?#";
        let intervalId = null;

        logo.addEventListener('mouseover', () => {
            let iteration = 0;
            clearInterval(intervalId);
            
            intervalId = setInterval(() => {
                logo.textContent = originalText.split("")
                    .map((letter, index) => {
                        if(index < iteration) {
                            return originalText[index];
                        }
                        return scrambleChars[Math.floor(Math.random() * scrambleChars.length)];
                    })
                    .join("");
                
                if(iteration >= originalText.length){ 
                    clearInterval(intervalId);
                    logo.textContent = originalText;
                }
                iteration += 1 / 3;
            }, 30);
        });
        
        logo.addEventListener('mouseleave', () => {
            clearInterval(intervalId);
            logo.textContent = originalText;
        });
    }

    let noise;
    try {
        if (typeof SimplexNoise === 'undefined') {
            throw new Error('SimplexNoise not loaded');
        }
        noise = new SimplexNoise();
    } catch (e) {
        console.warn("SimplexNoise library failed to load. Background blobs will not be animated with noise.");
        noise = {
            noise2D: (x, y) => Math.sin(x * 0.01) * Math.cos(y * 0.01)
        };
    }
    
    class Blob {
        constructor(ctx) {
            this.ctx = ctx;
            this.w = ctx.canvas.width / (window.devicePixelRatio || 1);
            this.h = ctx.canvas.height / (window.devicePixelRatio || 1);
            this.x = Math.random() * this.w;
            this.y = Math.random() * this.h;
            this.r = (Math.random() * 0.5 + 0.25) * Math.min(this.w, this.h);
            this.color = `rgba(0, 122, 255, ${Math.random() * 0.5 + 0.5})`;
            this.t = Math.random() * 1000;
            this.tInc = Math.random() * 0.003 + 0.002;
            this.xOff = Math.random() * 1000;
            this.yOff = Math.random() * 1000;
        }

        draw() {
            const points = [];
            const segments = 32;
            for (let i = 0; i < segments; i++) {
                const angle = (i / segments) * Math.PI * 2;
                const r = this.r * (1 + 0.2 * noise.noise2D(Math.cos(angle) + this.xOff + this.t, Math.sin(angle) + this.yOff + this.t));
                points.push({
                    x: this.x + r * Math.cos(angle),
                    y: this.y + r * Math.sin(angle)
                });
            }

            this.ctx.fillStyle = this.color;
            this.ctx.beginPath();
            this.ctx.moveTo(points[0].x, points[0].y);
            for (let i = 1; i < segments; i++) {
                this.ctx.lineTo(points[i].x, points[i].y);
            }
            this.ctx.closePath();
            this.ctx.fill();

            this.t += this.tInc;
        }
    }
    
    function initBlobs() {
        if (!blobCtx || isMobile) {
            if (blobCanvas) blobCanvas.style.display = 'none';
            return;
        }
        const dpr = window.devicePixelRatio || 1;
        blobCanvas.width = window.innerWidth * dpr;
        blobCanvas.height = window.innerHeight * dpr;
        blobCtx.scale(dpr, dpr);
        blobs = [new Blob(blobCtx), new Blob(blobCtx)];
    }

    function animateBlobs() {
        if (!blobCtx || isMobile) return;
        blobCtx.clearRect(0, 0, blobCanvas.width, blobCanvas.height);
        blobs.forEach(blob => blob.draw());
        requestAnimationFrame(animateBlobs);
    }

    // --- Confetti ---
    class ConfettiParticle {
        constructor(x, y, canvas) {
            this.x = x;
            this.y = y;
            this.canvas = canvas;
            this.ctx = canvas.getContext('2d');
            this.size = Math.random() * 5 + 2;
            this.color = confettiColors[Math.floor(Math.random() * confettiColors.length)];
            this.vx = (Math.random() - 0.5) * 10;
            this.vy = Math.random() * -10 - 5;
            this.opacity = 1;
            this.gravity = 0.3;
        }
        update() {
            this.vy += this.gravity;
            this.x += this.vx;
            this.y += this.vy;
            this.opacity -= 0.01;
        }
        draw() {
            this.ctx.globalAlpha = this.opacity;
            this.ctx.fillStyle = this.color;
            this.ctx.fillRect(this.x, this.y, this.size, this.size * 2);
            this.ctx.globalAlpha = 1;
        }
    }
    
    function launchConfetti(feedbackEl) {
        playSuccess();
        const canvas = feedbackEl.parentElement.querySelector('.confetti-canvas');
        if (!canvas) return;
        
        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        
        const dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        
        const particles = [];
        for (let i = 0; i < 50; i++) {
            particles.push(new ConfettiParticle(w / 2, h / 2, canvas));
        }
        
        function animateConfetti() {
            ctx.clearRect(0, 0, w, h);
            let allFaded = true;
            for (let i = 0; i < particles.length; i++) {
                particles[i].update();
                particles[i].draw();
                if (particles[i].opacity > 0) {
                    allFaded = false;
                }
            }
            if (!allFaded) {
                requestAnimationFrame(animateConfetti);
            } else {
                ctx.clearRect(0, 0, w, h);
            }
        }
        animateConfetti();
    }
    
    function initThreeJSVisual() {
        if (!heroVisualCanvas || typeof THREE === 'undefined') {
            console.error("three.js or canvas not found");
            return;
        }
        
        if (isMobile) {
            heroVisualCanvas.style.display = 'none';
            return;
        }

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, heroVisualCanvas.clientWidth / heroVisualCanvas.clientHeight, 0.1, 1000);
        camera.position.z = 2.5;

        const renderer = new THREE.WebGLRenderer({ 
            canvas: heroVisualCanvas,
            alpha: true
        });
        renderer.setSize(heroVisualCanvas.clientWidth, heroVisualCanvas.clientHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        const geometry = new THREE.IcosahedronGeometry(1.3, 0);
        const material = new THREE.MeshStandardMaterial({
            color: 0x4A9FDB,
            wireframe: true,
            metalness: 0.1,
            roughness: 0.2
        });
        const shape = new THREE.Mesh(geometry, material);
        scene.add(shape);

        const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
        scene.add(ambientLight);
        
        const pointLight = new THREE.PointLight(0x007AFF, 1, 100);
        pointLight.position.set(2, 3, 4);
        scene.add(pointLight);

        window.threejs = { camera, renderer };

        function animate() {
            requestAnimationFrame(animate);
            shape.rotation.y += 0.001;
            shape.rotation.x += 0.0005;
            renderer.render(scene, camera);
        }
        animate();
    }

    // --- NEW: Card Particle System ---
    class CardParticle {
        constructor(x, y) {
            this.x = x;
            this.y = y;
            this.vx = (Math.random() - 0.5) * 1.5;
            this.vy = Math.random() * -1.5 - 0.5;
            this.life = 1;
            this.size = Math.random() * 2 + 1;
        }
        
        update() {
            this.x += this.vx;
            this.y += this.vy;
            this.vy += 0.05; // gravity
            this.life -= 0.02;
        }
    }

    function initCardParticles() {
        if (isMobile) return; // Disable on mobile
        
        document.querySelectorAll('.bento-card, .application-card').forEach(card => {
            const canvas = card.querySelector('.card-particles');
            if (!canvas) return;
            
            const ctx = canvas.getContext('2d');
            let particles = [];
            let isHovering = false;

            const dpr = window.devicePixelRatio || 1;
            let rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            ctx.scale(dpr, dpr);
            
            // Resize listener
            new ResizeObserver(() => {
                rect = canvas.getBoundingClientRect();
                canvas.width = rect.width * dpr;
                canvas.height = rect.height * dpr;
                ctx.scale(dpr, dpr);
            }).observe(card);
            
            card.addEventListener('mouseenter', () => { isHovering = true; });
            card.addEventListener('mouseleave', () => { isHovering = false; });
            
            function animateParticles() {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                
                if (isHovering && particles.length < 50) {
                    particles.push(new CardParticle(Math.random() * canvas.width / dpr, canvas.height / dpr));
                }
                
                particles = particles.filter(p => p.life > 0);
                
                particles.forEach(p => {
                    p.update();
                    ctx.globalAlpha = p.life;
                    ctx.fillStyle = 'var(--accent-blue)';
                    ctx.fillRect(p.x, p.y, p.size, p.size);
                });
                
                ctx.globalAlpha = 1;
                requestAnimationFrame(animateParticles);
            }
            animateParticles();
        });
    }
    
    // --- NEW: Performance Chart ---
    function initPerformanceChart() {
        if (!chartCanvas) return;
        const ctx = chartCanvas.getContext('2d');
        
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: Array.from({length: 19}, (_, i) => i * 10 + '°'),
                datasets: [{
                    label: 'Simulated Torque (kg·cm)',
                    // Simple sine wave simulation
                    data: Array.from({length: 19}, (_, i) => 
                        (4.8 * Math.sin((i * 10 / 180) * Math.PI) + 0.2).toFixed(2)
                    ),
                    borderColor: 'var(--accent-blue)',
                    backgroundColor: 'rgba(0, 122, 255, 0.2)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 6,
                    pointBackgroundColor: 'var(--accent-blue)',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: { color: 'var(--text-primary)' } // CHANGED
                    },
                    title: {
                        display: true,
                        text: 'Torque vs. Angle (Simulated)',
                        color: 'var(--text-primary)', // CHANGED
                        font: { size: 16, family: "'Space Grotesk', sans-serif" }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(245, 245, 247, 0.1)' }, // CHANGED
                        ticks: { color: 'var(--text-primary)' } // CHANGED
                    },
                    x: {
                        grid: { color: 'rgba(245, 245, 247, 0.1)' }, // CHANGED
                        ticks: { color: 'var(--text-primary)' } // CHANGED
                    }
                }
            }
        });
    }
    
    // --- NEW: Real-Time Code Preview ---
    
    // Helper to be called from sandboxed code
    function setServoAngle(angle) {
        stopSweep();
        targetAngle = Math.max(0, Math.min(180, angle));
        lastDisplayedAngle = currentAngle;
    }

    // Helper to be called from sandboxed code
    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function runUserCode() {
        playClick();
        const code = codeAnswer.value;
        
        // Simple sanitization/replacement
        const safeCode = code
            .replace(/myservo\.write/g, 'await setServoAngle')
            .replace(/delay/g, 'await delay')
            .replace(/int\s/g, 'let ')
            .replace(/void\s/g, 'function ')
            .replace(/setup\s*\(\)/g, 'setup')
            .replace(/loop\s*\(\)/g, 'loop');

        try {
            // Create a sandboxed async function
            const func = new AsyncFunction('setServoAngle', 'delay', 'startSweep', 'stopSweep', safeCode);
            
            // Animate their code
            await func(setServoAngle, delay, startSweep, stopSweep);
            
            showNotification('✓ Code executed successfully!', 'success');
            updateProgress('code-challenge'); // Mark as complete
        } catch (err) {
            console.error('User code error:', err);
            playError();
            showNotification(`⚠ Error: ${err.message}`, 'error');
        }
    }
    
    // --- QUIZ JAVASCRIPT ---
    document.getElementById('ex1-btn')?.addEventListener('click', () => {
        playClick();
        const answer = parseFloat(document.getElementById('ex1-answer').value);
        const correct = 1.25;
        const feedback = document.getElementById('ex1-feedback');
        feedback.style.display = 'block';
        
        if (Math.abs(answer - correct) < 0.01) {
            feedback.textContent = '✅ Correct! 45° is 1/4 of the 180° range. 1.0ms + (1.0ms * 0.25) = 1.25ms';
            feedback.className = 'feedback correct';
            launchConfetti(feedback);
            updateProgress('ex1');
        } else {
            playError();
            feedback.textContent = `❌ Incorrect. Try again! (Your answer: ${answer || '...'}ms)`;
            feedback.className = 'feedback incorrect';
        }
    });
    document.getElementById('ex2-btn')?.addEventListener('click', () => {
        playClick();
        const answer = parseFloat(document.getElementById('ex2-answer').value);
        const correct = 8.75;
        const feedback = document.getElementById('ex2-feedback');
        feedback.style.display = 'block';
        
        if (Math.abs(answer - correct) < 0.1) {
            feedback.textContent = '✅ Correct! (1.75ms / 20ms) * 100 = 8.75%';
            feedback.className = 'feedback correct';
            launchConfetti(feedback);
            updateProgress('ex2');
        } else {
            playError();
            feedback.textContent = `❌ Incorrect. Try again! (Your answer: ${answer || '...'}%)`;
            feedback.className = 'feedback incorrect';
        }
    });
    document.getElementById('ex3-btn')?.addEventListener('click', () => {
        playClick();
        const answer = parseFloat(document.getElementById('ex3-answer').value);
        const correct = 800;
        const feedback = document.getElementById('ex3-feedback');
        feedback.style.display = 'block';
        
        if (answer === correct) {
            feedback.textContent = '✅ Correct! 4 servos × 200mA = 800mA (or 0.8A)';
            feedback.className = 'feedback correct';
            launchConfetti(feedback);
            updateProgress('ex3');
        } else {
            playError();
            feedback.textContent = `❌ Incorrect. Try again! (Your answer: ${answer || '...'}mA)`;
            feedback.className = 'feedback incorrect';
        }
    });
    document.getElementById('ex4-btn')?.addEventListener('click', () => {
        playClick();
        const answer = parseFloat(document.getElementById('ex4-answer').value);
        const correct = 50;
        const feedback = document.getElementById('ex4-feedback');
        feedback.style.display = 'block';
        
        if (answer === correct) {
            feedback.textContent = '✅ Correct! 1 / 0.020 seconds = 50 Hz';
            feedback.className = 'feedback correct';
            launchConfetti(feedback);
            updateProgress('ex4');
        } else {
            playError();
            feedback.textContent = `❌ Incorrect. Try again! (Your answer: ${answer || '...'}Hz)`;
            feedback.className = 'feedback incorrect';
        }
    });

    // Add listeners for hint buttons
    document.getElementById('ex1-hint-btn')?.addEventListener('click', () => {
        playClick();
        document.getElementById('ex1-hint').classList.toggle('hidden');
    });
    document.getElementById('ex2-hint-btn')?.addEventListener('click', () => {
        playClick();
        document.getElementById('ex2-hint').classList.toggle('hidden');
    });
    document.getElementById('ex3-hint-btn')?.addEventListener('click', () => {
        playClick();
        document.getElementById('ex3-hint').classList.toggle('hidden');
    });
    document.getElementById('ex4-hint-btn')?.addEventListener('click', () => {
        playClick();
        document.getElementById('ex4-hint').classList.toggle('hidden');
    });
    
    // Add listener for Run Code button
    if (runCodeBtn) {
        runCodeBtn.addEventListener('click', runUserCode);
    }

    // --- Keyboard Controls ---
    document.addEventListener('keydown', (e) => {
        // Close overlays with Escape
        if (e.key === 'Escape') {
            const overlay = document.getElementById('shortcuts-overlay');
            if (overlay && overlay.classList.contains('visible')) {
                overlay.classList.remove('visible');
                setTimeout(() => overlay.remove(), 300);
            }
            if (presentationMode) {
                togglePresentationMode();
            }
        }

        // Show shortcuts with ?
        if (e.key === '?' && !e.ctrlKey && !e.shiftKey) {
             if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;
             e.preventDefault();
             showShortcutsHelp();
        }
        
        // Presentation Mode
        if (e.key === 'p' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            togglePresentationMode();
        }
        
        // Annotation Mode
        if (e.key === 'd' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            toggleDrawingMode();
        }
        
        // Laser Pointer
        if (e.key === 'l' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            toggleSpotlight();
        }
        
        // Performance Monitor
        if (e.key === 'P' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
            e.preventDefault();
            togglePerfMonitor();
        }
        
        // Clear Annotations
        if (e.key === 'c' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
            e.preventDefault();
            clearAnnotations();
        }

        // Stop if an input is focused
        if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') {
            return;
        }

        let newAngle = -1;
        switch(e.key) {
            case '0': newAngle = 0; break;
            case '9': newAngle = 90; break;
            case '8': newAngle = 180; break;
            case 's':
            case 'S':
                if (autoSweep) stopSweep();
                else startSweep();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                newAngle = Math.max(0, targetAngle - 5);
                break;
            case 'ArrowRight':
                e.preventDefault();
                newAngle = Math.min(180, targetAngle + 5);
                break;
        }

        if (newAngle !== -1) {
            e.preventDefault();
            stopSweep();
            targetAngle = newAngle;
            lastDisplayedAngle = currentAngle;
        }
    });
    
    // --- Annotation Listeners ---
    annotationCanvas.addEventListener('mousedown', (e) => {
        if (!drawingMode) return;
        isDrawing = true;
        drawings.push([]);
        drawings[drawings.length - 1].push({ x: e.clientX, y: e.clientY });
        redrawAnnotations();
    });

    annotationCanvas.addEventListener('mousemove', (e) => {
        if (spotlightActive) {
            laserSpotlight.style.left = e.clientX + 'px';
            laserSpotlight.style.top = e.clientY + 'px';
        }
        
        if (!isDrawing || !drawingMode) return;
        
        drawings[drawings.length - 1].push({ x: e.clientX, y: e.clientY });
        redrawAnnotations();
    });

    annotationCanvas.addEventListener('mouseup', () => {
        isDrawing = false;
    });
    
    function redrawAnnotations() {
        const dpr = window.devicePixelRatio || 1;
        annotationCtx.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
        annotationCtx.strokeStyle = 'rgba(243, 156, 18, 0.8)';
        annotationCtx.lineWidth = 4 * dpr;
        annotationCtx.lineCap = 'round';
        annotationCtx.lineJoin = 'round';
        annotationCtx.shadowColor = 'rgba(243, 156, 18, 0.8)';
        annotationCtx.shadowBlur = 8;
        
        drawings.forEach(line => {
            if (line.length < 2) return;
            
            annotationCtx.beginPath();
            annotationCtx.moveTo(line[0].x * dpr, line[0].y * dpr);
            
            for (let i = 1; i < line.length; i++) {
                annotationCtx.lineTo(line[i].x * dpr, line[i].y * dpr);
            }
            
            annotationCtx.stroke();
        });
    }

    // --- Lazy Load Animations ---
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '50px'
    };

    let heroAnimated = false;
    const animationObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                if (entry.target.id === 'hero' && !heroAnimated) {
                    safeInit(initConstellation, 'Constellation');
                    safeInit(initThreeJSVisual, 'Three.js Visual');
                    heroAnimated = true;
                    animationObserver.unobserve(hero);
                }
            }
        });
    }, observerOptions);

    if (hero) {
        animationObserver.observe(hero);
    }


    // --- Initialization ---
    function main() {
        if (slider) {
            targetAngle = parseFloat(slider.value);
        } else {
            targetAngle = 90;
        }
        
        currentAngle = targetAngle;
        lastDisplayedAngle = targetAngle;
        updateVisuals(currentAngle);
        
        setTimeout(() => {
            if(pwmCtx) {
                drawPWM(currentAngle);
            }
        }, 100);
        
        // Setup annotation canvas size
        annotationCanvas.width = window.innerWidth * window.devicePixelRatio;
        annotationCanvas.height = window.innerHeight * window.devicePixelRatio;
        annotationCtx.scale(window.devicePixelRatio, window.devicePixelRatio);


        safeInit(initScrollAnimations, 'Scroll Animations');
        safeInit(() => smoothLoop(), 'Smooth Loop');
        
        if (blobCtx) {
            safeInit(initBlobs, 'Background Blobs');
            safeInit(animateBlobs, 'Background Blobs Animate');
        }
        
        safeInit(initLogoScramble, 'Logo Scramble');
        
        // NEW: Init new features
        safeInit(initPerformanceChart, 'Perf Chart');
        safeInit(initCardParticles, 'Card Particles');
        safeInit(loadProgress, 'Load Progress');
        
        // Init Prism
        if (typeof Prism !== 'undefined') {
            safeInit(Prism.highlightAll, 'Prism Syntax Highlighting');
        } else {
            console.warn('Prism.js not loaded, deferring highlighting.');
            window.addEventListener('load', () => safeInit(Prism.highlightAll, 'Prism Syntax Highlighting'));
        }
    }

    main();