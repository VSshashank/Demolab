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

// --- Main Module Script ---

// --- Performance Consts ---
const isMobile = window.matchMedia('(max-width: 768px)').matches;
const isLowPerformance = navigator.hardwareConcurrency < 4;

// --- DOM Elements ---
const slider = document.getElementById('temp-slider');
const sliderWrapper = document.querySelector('.slider-wrapper');
const lcdLine1 = document.getElementById('lcd-line-1');
const lcdLine2 = document.getElementById('lcd-line-2');
const tempCValue = document.getElementById('temp-c-value');
const tempFValue = document.getElementById('temp-f-value');
const voltageValue = document.getElementById('voltage-value');

const voltageCanvas = document.getElementById('voltage-canvas');
const voltageCtx = voltageCanvas ? voltageCanvas.getContext('2d') : null;

const btn0 = document.getElementById('btn-0');
const btn22 = document.getElementById('btn-22');
const btn100 = document.getElementById('btn-100');
const btnAuto = document.getElementById('btn-auto');
const copyCodeBtn = document.getElementById('copy-code');

const hero = document.getElementById('hero');
const heroInner = document.querySelector('.hero-inner');
const heroConstellation = document.getElementById('hero-constellation');
const scrollIndicator = document.querySelector('.scroll-indicator');

const heroVisualCanvas = document.getElementById('hero-3d-visual');
const logo = document.querySelector('.logo');

const blobCanvas = document.getElementById('background-blobs');
const blobCtx = blobCanvas ? blobCanvas.getContext('2d') : null;
let blobs = [];

// Code Preview
const runCodeBtn = document.getElementById('run-code-btn');
const codeAnswer = document.getElementById('code-answer');

// Learning Progress
const progressBarLabel = document.getElementById('progress-bar-label');
const progressBarInner = document.getElementById('progress-bar-inner');
const achievementToast = document.getElementById('achievement-toast');
const QUIZ_COUNT = 5;
let learningProgress = {
    completedQuizzes: new Set(),
    achievements: new Set()
};

// Sound Effects
let audioCtx;
let mainGain;
try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    mainGain = audioCtx.createGain();
    mainGain.gain.value = 0.3;
    mainGain.connect(audioCtx.destination);
} catch (e) {
    console.warn("Web Audio API not supported. Sound effects will be disabled.", e);
}

// Presentation Mode
let presentationMode = false;

// Annotation
const annotationCanvas = document.getElementById('annotation-canvas');
const annotationCtx = annotationCanvas.getContext('2d');
let isDrawing = false;
let drawingMode = false;
let drawings = [];

// Laser Pointer
const laserSpotlight = document.getElementById('laser-spotlight');
let spotlightActive = false;

// Performance Monitor
const perfMonitor = document.getElementById('perf-monitor');
let frameCount = 0;
let lastTime = performance.now();
let fps = 60;
let perfMonitorVisible = false;

// Auto-Cycle
let autoCycle = false;
let cycleDirection = 1;
let cycleSpeed = 0.2;

// --- Animation State ---
let targetTemp = 22.5;
let currentTemp = 22.5;
let lastDisplayedTemp = 22.5;

// --- Sound Functions ---
function playSound(type, freq, duration, gain = 0.3) {
    if (!audioCtx) return;
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

// --- Learning Progress Functions ---
function loadProgress() {
    try {
        const saved = localStorage.getItem('tempview_progress');
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
        localStorage.setItem('tempview_progress', JSON.stringify({
            completedQuizzes: [...learningProgress.completedQuizzes],
            achievements: [...learningProgress.achievements]
        }));
    } catch (e) {
        console.warn('Could not save progress to localStorage', e);
    }
}

function updateProgress(quizId) {
    if (learningProgress.completedQuizzes.has(quizId)) return;
    
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
    if (learningProgress.achievements.has(id)) return;
    
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
    playSuccess();
    
    setTimeout(() => {
        achievementToast.classList.remove('show');
    }, 4000);
}

// --- Update Visuals Function ---
function updateVisuals(tempC) {
    if (slider && Math.abs(parseFloat(slider.value) - tempC) > 0.1) {
        slider.value = tempC;
    }
    
    // Update readouts
    animateValue(tempCValue, lastDisplayedTemp, tempC, 150, 1, '°');
    const tempF = tempC * 1.8 + 32;
    tempFValue.textContent = tempF.toFixed(1) + '°';
    
    // Calculate voltage
    const voltage = (tempC * 0.01) + 0.5;
    voltageValue.textContent = voltage.toFixed(2) + ' V';
    
    // Update LCD display
    lcdLine1.textContent = `Temp: ${tempC.toFixed(2)} C`;
    lcdLine2.textContent = `Voltage: ${voltage.toFixed(2)} V`;
    
    // Update voltage graph
    if (voltageCtx) {
        drawVoltageGraph(tempC, voltage);
    }
    
    lastDisplayedTemp = tempC;
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
            if (slider && element.id === 'temp-c-value') {
                slider.setAttribute('aria-valuenow', end.toFixed(1));
                slider.setAttribute('aria-valuetext', `${end.toFixed(1)} degrees Celsius`);
            }
        }
    }
    requestAnimationFrame(update);
}

function drawVoltageGraph(tempC, voltage) {
    if (!voltageCtx || !voltageCanvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = voltageCanvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    
    voltageCanvas.width = rect.width * dpr;
    voltageCanvas.height = rect.height * dpr;
    voltageCtx.scale(dpr, dpr);
    
    const w = rect.width;
    const h = rect.height;

    voltageCtx.clearRect(0, 0, w, h);
    
    const padding = 20;
    const graphWidth = w - 2 * padding;
    const graphHeight = h - 2 * padding;
    
    const minTemp = -40;
    const maxTemp = 125;
    const minVoltage = (minTemp * 0.01) + 0.5;
    const maxVoltage = (maxTemp * 0.01) + 0.5;
    
    // Draw axes - UPDATED COLORS for better visibility
    voltageCtx.strokeStyle = '#ffffff'; // Changed from var(--text-secondary) to white
    voltageCtx.lineWidth = 2; // Increased line width
    
    // X-axis (Temperature)
    voltageCtx.beginPath();
    voltageCtx.moveTo(padding, padding + graphHeight);
    voltageCtx.lineTo(padding + graphWidth, padding + graphHeight);
    voltageCtx.stroke();
    
    // Y-axis (Voltage)
    voltageCtx.beginPath();
    voltageCtx.moveTo(padding, padding);
    voltageCtx.lineTo(padding, padding + graphHeight);
    voltageCtx.stroke();
    
    // Draw grid - UPDATED COLORS for better visibility
    voltageCtx.strokeStyle = 'rgba(255, 255, 255, 0.3)'; // Increased opacity
    voltageCtx.lineWidth = 1;
    
    // Grid lines for temperature
    for (let temp = -40; temp <= 125; temp += 20) {
        const x = padding + ((temp - minTemp) / (maxTemp - minTemp)) * graphWidth;
        voltageCtx.beginPath();
        voltageCtx.moveTo(x, padding);
        voltageCtx.lineTo(x, padding + graphHeight);
        voltageCtx.stroke();
    }
    
    // Grid lines for voltage
    for (let v = 0.1; v <= 1.8; v += 0.2) {
        const y = padding + graphHeight - ((v - minVoltage) / (maxVoltage - minVoltage)) * graphHeight;
        voltageCtx.beginPath();
        voltageCtx.moveTo(padding, y);
        voltageCtx.lineTo(padding + graphWidth, y);
        voltageCtx.stroke();
    }
    
    // Draw linear relationship - UPDATED COLOR for better visibility
    voltageCtx.strokeStyle = '#00FFAA'; // Changed to bright cyan-green for better contrast
    voltageCtx.lineWidth = 3;
    voltageCtx.beginPath();
    
    for (let temp = minTemp; temp <= maxTemp; temp += 5) {
        const v = (temp * 0.01) + 0.5;
        const x = padding + ((temp - minTemp) / (maxTemp - minTemp)) * graphWidth;
        const y = padding + graphHeight - ((v - minVoltage) / (maxVoltage - minVoltage)) * graphHeight;
        
        if (temp === minTemp) {
            voltageCtx.moveTo(x, y);
        } else {
            voltageCtx.lineTo(x, y);
        }
    }
    voltageCtx.stroke();
    
    // Draw current point - UPDATED COLOR for better visibility
    const currentX = padding + ((tempC - minTemp) / (maxTemp - minTemp)) * graphWidth;
    const currentY = padding + graphHeight - ((voltage - minVoltage) / (maxVoltage - minVoltage)) * graphHeight;
    
    voltageCtx.fillStyle = '#FF6B6B'; // Changed to bright coral for better visibility
    voltageCtx.beginPath();
    voltageCtx.arc(currentX, currentY, 8, 0, Math.PI * 2); // Increased size
    voltageCtx.fill();
    
    // Add a white border to the point for even better visibility
    voltageCtx.strokeStyle = '#ffffff';
    voltageCtx.lineWidth = 2;
    voltageCtx.stroke();
    
    // Draw labels - UPDATED COLORS for better visibility
    voltageCtx.fillStyle = '#ffffff'; // White text
    voltageCtx.font = "12px 'Space Grotesk', sans-serif"; // Slightly larger font
    voltageCtx.textAlign = 'center';
    
    // X-axis labels
    voltageCtx.fillText('-40°C', padding, padding + graphHeight + 18);
    voltageCtx.fillText('0°C', padding + ((-minTemp) / (maxTemp - minTemp)) * graphWidth, padding + graphHeight + 18);
    voltageCtx.fillText('125°C', padding + graphWidth, padding + graphHeight + 18);
    
    // Y-axis labels
    voltageCtx.textAlign = 'right';
    voltageCtx.fillText('0.1V', padding - 5, padding + graphHeight);
    voltageCtx.fillText('0.5V', padding - 5, padding + graphHeight - ((0.5 - minVoltage) / (maxVoltage - minVoltage)) * graphHeight);
    voltageCtx.fillText('1.75V', padding - 5, padding);
    
    // Add title for better context
    voltageCtx.textAlign = 'center';
    voltageCtx.fillStyle = '#ffffff';
    voltageCtx.font = "bold 14px 'Space Grotesk', sans-serif";
    voltageCtx.fillText('Voltage vs Temperature', w / 2, 15);
}

// --- Animation Loop ---
function smoothLoop() {
    if (autoCycle) {
        let newTemp = currentTemp + (cycleSpeed * cycleDirection);
        if (newTemp >= 125) { newTemp = 125; cycleDirection = -1; }
        if (newTemp <= -40) { newTemp = -40; cycleDirection = 1; }
        targetTemp = newTemp;
    }

    const speed = Math.abs(currentTemp - targetTemp);
    if (speed < 0.05) {
        currentTemp = targetTemp;
    } else {
        currentTemp += (targetTemp - currentTemp) * 0.15;
    }
    
    updateVisuals(currentTemp);
    
    requestAnimationFrame(smoothLoop);
}

// --- Event Listeners ---
if (slider) {
    slider.addEventListener('input', (e) => {
        playClick();
        stopAutoCycle();
        targetTemp = parseFloat(e.target.value);
        lastDisplayedTemp = currentTemp;
    });
}

function setTemperature(temp) {
    playClick();
    stopAutoCycle();
    targetTemp = temp;
    lastDisplayedTemp = currentTemp;
}

function toggleAutoCycle() {
    playClick();
    autoCycle = !autoCycle;
    if (autoCycle) {
        btnAuto.classList.add('active');
        btnAuto.textContent = 'Stop Cycle';
        btnAuto.setAttribute('aria-label', 'Stop auto temperature cycle');
    } else {
        btnAuto.classList.remove('active');
        btnAuto.textContent = 'Auto Cycle';
        btnAuto.setAttribute('aria-label', 'Toggle auto temperature cycle');
    }
}

function stopAutoCycle() {
    autoCycle = false;
    if (btnAuto) {
        btnAuto.classList.remove('active');
        btnAuto.textContent = 'Auto Cycle';
        btnAuto.setAttribute('aria-label', 'Toggle auto temperature cycle');
    }
}

if (btn0) btn0.addEventListener('click', () => setTemperature(0));
if (btn22) btn22.addEventListener('click', () => setTemperature(22));
if (btn100) btn100.addEventListener('click', () => setTemperature(100));
if (btnAuto) btnAuto.addEventListener('click', toggleAutoCycle);

// --- Three.js 3D Visual ---
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

    // Create a thermometer-like capsule
    const capsuleGeometry = new THREE.CapsuleGeometry(0.5, 1.5, 4, 8);
    const material = new THREE.MeshStandardMaterial({
        color: 0x007AFF,
        wireframe: true,
        metalness: 0.1,
        roughness: 0.2
    });
    const shape = new THREE.Mesh(capsuleGeometry, material);
    scene.add(shape);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambientLight);
    
    const pointLight = new THREE.PointLight(0x007AFF, 1, 100);
    pointLight.position.set(2, 3, 4);
    scene.add(pointLight);

    function animate() {
        requestAnimationFrame(animate);
        shape.rotation.y += 0.001;
        shape.rotation.x += 0.0005;
        renderer.render(scene, camera);
    }
    animate();
}

// --- Background Blobs ---
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

// --- Constellation Background ---
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
    const dotCount = isMobile ? 25 : 50;
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

// --- Card Particle System ---
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
        this.vy += 0.05;
        this.life -= 0.02;
    }
}

function initCardParticles() {
    if (isMobile) return;
    
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

// --- Scroll Animations ---
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

// --- Quiz Logic ---
document.getElementById('ex1-btn')?.addEventListener('click', () => {
    playClick();
    const answer = parseFloat(document.getElementById('ex1-answer').value);
    const correct = 25.0; // (0.75 - 0.5) * 100
    const feedback = document.getElementById('ex1-feedback');
    feedback.style.display = 'block';
    
    if (Math.abs(answer - correct) < 0.1) {
        feedback.textContent = '✅ Correct! (0.75 - 0.50) × 100 = 25°C';
        feedback.className = 'feedback correct';
        updateProgress('ex1');
    } else {
        playError();
        feedback.textContent = `❌ Incorrect. Try again! Hint: (0.75 - 0.5) × 100 = 25`;
        feedback.className = 'feedback incorrect';
    }
});

document.getElementById('ex2-btn')?.addEventListener('click', () => {
    playClick();
    const answer = parseFloat(document.getElementById('ex2-answer').value);
    const correct = 0.95; // (45 × 0.01) + 0.5
    const feedback = document.getElementById('ex2-feedback');
    feedback.style.display = 'block';
    
    if (Math.abs(answer - correct) < 0.01) {
        feedback.textContent = '✅ Correct! (45 × 0.01) + 0.5 = 0.95V';
        feedback.className = 'feedback correct';
        updateProgress('ex2');
    } else {
        playError();
        feedback.textContent = `❌ Incorrect. Try again! Hint: (45 × 0.01) + 0.5 = 0.95`;
        feedback.className = 'feedback incorrect';
    }
});

document.getElementById('ex3-btn')?.addEventListener('click', () => {
    playClick();
    const answer = parseFloat(document.getElementById('ex3-answer').value);
    const correct = 0.4; // (-10 × 0.01) + 0.5
    const feedback = document.getElementById('ex3-feedback');
    feedback.style.display = 'block';
    
    if (Math.abs(answer - correct) < 0.01) {
        feedback.textContent = '✅ Correct! (-10 × 0.01) + 0.5 = 0.4V';
        feedback.className = 'feedback correct';
        updateProgress('ex3');
    } else {
        playError();
        feedback.textContent = `❌ Incorrect. Try again! Hint: (-10 × 0.01) + 0.5 = 0.4`;
        feedback.className = 'feedback incorrect';
    }
});

document.getElementById('ex4-btn')?.addEventListener('click', () => {
    playClick();
    const answer = parseFloat(document.getElementById('ex4-answer').value);
    const correct = 2.5; // (512 × 5.0 / 1024.0 - 0.5) × 100
    const feedback = document.getElementById('ex4-feedback');
    feedback.style.display = 'block';
    
    if (Math.abs(answer - correct) < 0.1) {
        feedback.textContent = '✅ Correct! Voltage = 2.5V, Temp = 2.5°C';
        feedback.className = 'feedback correct';
        updateProgress('ex4');
    } else {
        playError();
        feedback.textContent = `❌ Incorrect. Try: (512 × 5.0 / 1024.0 - 0.5) × 100 = 2.5`;
        feedback.className = 'feedback incorrect';
    }
});

document.getElementById('run-code-btn')?.addEventListener('click', () => {
    playClick();
    const code = codeAnswer.value;
    const feedback = document.getElementById('code-feedback');
    feedback.style.display = 'block';
    
    if (code.includes('voltage') && code.includes('0.5') && code.includes('100')) {
        feedback.textContent = '✅ Logic looks good! Correct formula: (voltage - 0.5) * 100';
        feedback.className = 'feedback correct';
        updateProgress('code-challenge');
    } else {
        playError();
        feedback.textContent = '❌ Missing key logic. Should be: (voltage - 0.5) * 100';
        feedback.className = 'feedback incorrect';
    }
});

// Hint buttons
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

// --- Copy Code Button ---
if (copyCodeBtn) {
    copyCodeBtn.addEventListener('click', () => {
        playClick();
        const code = document.getElementById('code-block').innerText;
        navigator.clipboard.writeText(code).then(() => {
            copyCodeBtn.textContent = 'Copied!';
            setTimeout(() => {
                copyCodeBtn.textContent = 'Copy';
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy text: ', err);
            copyCodeBtn.textContent = 'Error';
        });
    });
}

// --- Window Events ---
window.addEventListener('resize', () => {
    if (voltageCtx) {
        drawVoltageGraph(currentTemp, (currentTemp * 0.01) + 0.5);
    }
    if (heroVisualCanvas) {
        const camera = heroVisualCanvas._camera;
        const renderer = heroVisualCanvas._renderer;
        if (camera && renderer) {
            camera.aspect = heroVisualCanvas.clientWidth / heroVisualCanvas.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(heroVisualCanvas.clientWidth, heroVisualCanvas.clientHeight);
        }
    }
    if (blobCtx) {
        initBlobs();
    }
    if (heroConstellation) {
        initConstellation();
    }
});

window.addEventListener('scroll', () => {
    if (presentationMode) return;
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

// --- Presentation Mode Functions ---
function togglePresentationMode() {
    presentationMode = !presentationMode;
    playClick();
    document.body.classList.toggle('presentation-mode');
}

function toggleLaser() {
    playClick();
    spotlightActive = !spotlightActive;
    laserSpotlight.style.display = spotlightActive ? 'block' : 'none';
}

function clearAnnotations() {
    drawings.length = 0;
    annotationCtx.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
}

// Initialize annotation canvas
annotationCanvas.width = window.innerWidth * window.devicePixelRatio;
annotationCanvas.height = window.innerHeight * window.devicePixelRatio;
annotationCtx.scale(window.devicePixelRatio, window.devicePixelRatio);

annotationCanvas.addEventListener('mousedown', (e) => {
    if (!drawingMode) return;
    isDrawing = true;
    drawings.push([]);
    drawings[drawings.length - 1].push({ x: e.clientX, y: e.clientY });
});

annotationCanvas.addEventListener('mousemove', (e) => {
    if (spotlightActive) {
        laserSpotlight.style.left = e.clientX + 'px';
        laserSpotlight.style.top = e.clientY + 'px';
    }
    
    if (!isDrawing || !drawingMode) return;
    
    drawings[drawings.length - 1].push({ x: e.clientX, y: e.clientY });
    
    // Redraw
    const dpr = window.devicePixelRatio || 1;
    annotationCtx.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
    annotationCtx.strokeStyle = 'rgba(243, 156, 18, 0.8)';
    annotationCtx.lineWidth = 4 * dpr;
    annotationCtx.lineCap = 'round';
    annotationCtx.lineJoin = 'round';
    
    drawings.forEach(line => {
        if (line.length < 2) return;
        
        annotationCtx.beginPath();
        annotationCtx.moveTo(line[0].x * dpr, line[0].y * dpr);
        
        for (let i = 1; i < line.length; i++) {
            annotationCtx.lineTo(line[i].x * dpr, line[i].y * dpr);
        }
        
        annotationCtx.stroke();
    });
});

annotationCanvas.addEventListener('mouseup', () => {
    isDrawing = false;
});

// --- Keyboard Shortcuts ---
document.addEventListener('keydown', (e) => {
    // Escape closes overlays/presentation
    if (e.key === 'Escape') {
        if (presentationMode) {
            togglePresentationMode();
        }
    }
    
    // Presentation mode
    if (e.key === 'p' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        togglePresentationMode();
    }
    
    // Laser pointer
    if (e.key === 'l' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        toggleLaser();
    }
    
    // Clear drawings
    if (e.key === 'c' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
        e.preventDefault();
        clearAnnotations();
    }
    
    // Stop if input is focused
    if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') {
        return;
    }
    
    // Temperature presets
    let newTemp = -1;
    switch(e.key) {
        case '0': newTemp = 0; break;
        case '2': newTemp = 22; break;
        case '1': newTemp = 100; break;
        case 'a':
        case 'A':
            toggleAutoCycle();
            break;
        case 'ArrowUp':
            e.preventDefault();
            newTemp = Math.min(125, targetTemp + 5);
            break;
        case 'ArrowDown':
            e.preventDefault();
            newTemp = Math.max(-40, targetTemp - 5);
            break;
    }
    
    if (newTemp !== -1) {
        e.preventDefault();
        setTemperature(newTemp);
    }
});

// --- Main Initialization ---
function main() {
    if (slider) {
        targetTemp = parseFloat(slider.value);
    }
    
    currentTemp = targetTemp;
    lastDisplayedTemp = targetTemp;
    updateVisuals(currentTemp);
    
    setTimeout(() => {
        if (voltageCtx) {
            drawVoltageGraph(currentTemp, (currentTemp * 0.01) + 0.5);
        }
    }, 100);
    
    // Initialize components
    initScrollAnimations();
    smoothLoop();
    
    if (blobCtx) {
        initBlobs();
        animateBlobs();
    }
    
    initConstellation();
    initThreeJSVisual();
    initCardParticles();
    loadProgress();
    
    // Initialize Prism
    if (typeof Prism !== 'undefined') {
        Prism.highlightAll();
    }
}

// Start everything
main();