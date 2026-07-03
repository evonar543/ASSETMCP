import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const canvas = document.getElementById("world");
const menu = document.getElementById("menu");
const startButton = document.getElementById("start");
const biomeText = document.getElementById("biome");
const positionText = document.getElementById("position");

const CHARACTER_GLB = "../assets/flat_world-character-kenney/Models/GLB%20format/character-a.glb";
const GRASS_TEXTURE = "../assets/zombie_fps/PNG/floor_ground_grass.png";
const SAND_TEXTURE = "../assets/zombie_fps/PNG/floor_ground_sand.png";
const DIRT_TEXTURE = "../assets/zombie_fps/PNG/floor_ground_dirt.png";

const CHUNK_SIZE = 24;
const TILE_SIZE = 3;
const VIEW_RADIUS = 3;
const PLAYER_SPEED = 7.6;
const SPRINT_MULT = 1.55;
const ATTACK_DURATION = 0.42;
const ENEMY_COUNT = 8;

const keys = new Set();
const chunks = new Map();
const clock = new THREE.Clock();
const moveVector = new THREE.Vector3();
const cameraForward = new THREE.Vector3();
const cameraRight = new THREE.Vector3();

let renderer;
let scene;
let camera;
let player;
let character;
let characterRig;
let fallbackCharacter;
let enemies = [];
let attackTime = 0;
let cameraYaw = Math.PI;
let orbitPitch = 0.74;
let dragging = false;
let running = false;

function hash2(x, z) {
  let n = x * 374761393 + z * 668265263 + 0x9e3779b9;
  n = (n ^ (n >> 13)) * 1274126177;
  return ((n ^ (n >> 16)) >>> 0) / 4294967295;
}

function smoothNoise(x, z) {
  const ix = Math.floor(x);
  const iz = Math.floor(z);
  const fx = x - ix;
  const fz = z - iz;
  const sx = fx * fx * (3 - 2 * fx);
  const sz = fz * fz * (3 - 2 * fz);
  const a = hash2(ix, iz);
  const b = hash2(ix + 1, iz);
  const c = hash2(ix, iz + 1);
  const d = hash2(ix + 1, iz + 1);
  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(a, b, sx), THREE.MathUtils.lerp(c, d, sx), sz);
}

function biomeAt(x, z) {
  const value = smoothNoise(x * 0.025, z * 0.025) * 0.65 + smoothNoise(x * 0.07 + 30, z * 0.07 - 10) * 0.35;
  if (value < 0.34) return "Sand Flats";
  if (value > 0.68) return "Pine Meadow";
  return "Meadow";
}

function setupRenderer() {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.6));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9dc7dc);
  scene.fog = new THREE.Fog(0x9dc7dc, 72, 210);

  camera = new THREE.PerspectiveCamera(62, 1, 0.1, 420);
  scene.add(camera);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x55705d, 2.2);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight(0xfff1d0, 3.1);
  sun.position.set(-42, 58, 26);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -70;
  sun.shadow.camera.right = 70;
  sun.shadow.camera.top = 70;
  sun.shadow.camera.bottom = -70;
  scene.add(sun);

  resize();
}

function makeTexture(path, repeat = 8) {
  const tex = new THREE.TextureLoader().load(path);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.magFilter = THREE.NearestFilter;
  return tex;
}

const materials = {};

function setupMaterials() {
  materials.meadow = new THREE.MeshStandardMaterial({ map: makeTexture(GRASS_TEXTURE), roughness: 0.95 });
  materials.sand = new THREE.MeshStandardMaterial({ map: makeTexture(SAND_TEXTURE), roughness: 1 });
  materials.dirt = new THREE.MeshStandardMaterial({ map: makeTexture(DIRT_TEXTURE), roughness: 1 });
  materials.tree = new THREE.MeshStandardMaterial({ color: 0x2e6e43, roughness: 0.78 });
  materials.trunk = new THREE.MeshStandardMaterial({ color: 0x725132, roughness: 0.92 });
  materials.rock = new THREE.MeshStandardMaterial({ color: 0x717a78, roughness: 0.86 });
  materials.marker = new THREE.MeshStandardMaterial({ color: 0xfff2a8, emissive: 0x4d3500, emissiveIntensity: 0.25 });
  materials.enemySkin = new THREE.MeshStandardMaterial({ color: 0x86b66f, roughness: 0.82 });
  materials.enemyCloth = new THREE.MeshStandardMaterial({ color: 0x7c3241, roughness: 0.78 });
  materials.swordBlade = new THREE.MeshStandardMaterial({
    color: 0xf4fbff,
    emissive: 0x324c58,
    emissiveIntensity: 0.18,
    metalness: 0.55,
    roughness: 0.3,
  });
  materials.swordGrip = new THREE.MeshStandardMaterial({ color: 0x453022, roughness: 0.8 });
  materials.slash = new THREE.MeshBasicMaterial({
    color: 0x8fe8ff,
    transparent: true,
    opacity: 0.55,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
}

function createFallbackHumanoid() {
  const group = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: 0xd3a276, roughness: 0.82 });
  const shirt = new THREE.MeshStandardMaterial({ color: 0x2f80ed, roughness: 0.76 });
  const pants = new THREE.MeshStandardMaterial({ color: 0x26384f, roughness: 0.82 });
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.7, 0.7), skin);
  head.position.y = 2.55;
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.86, 1.1, 0.42), shirt);
  body.position.y = 1.7;
  const armL = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.92, 0.28), skin);
  armL.position.set(-0.66, 1.72, 0);
  const armR = armL.clone();
  armR.position.x = 0.66;
  const legL = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.95, 0.32), pants);
  legL.position.set(-0.23, 0.62, 0);
  const legR = legL.clone();
  legR.position.x = 0.23;
  group.add(head, body, armL, armR, legL, legR);
  group.userData.parts = { armL, armR, legL, legR };
  group.traverse((child) => {
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });
  return group;
}

function createSword() {
  const sword = new THREE.Group();
  const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 1.36, 0.1), materials.swordBlade);
  const guard = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.08, 0.14), materials.swordBlade);
  const grip = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.38, 0.14), materials.swordGrip);
  blade.position.y = 0.6;
  guard.position.y = -0.08;
  grip.position.y = -0.28;
  sword.add(blade, guard, grip);
  sword.traverse((child) => {
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });
  return sword;
}

function createEnemy() {
  const group = new THREE.Group();
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.62, 0.62), materials.enemySkin);
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.78, 0.9, 0.42), materials.enemyCloth);
  const armLeft = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.82, 0.24), materials.enemySkin);
  const armRight = armLeft.clone();
  const legLeft = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.82, 0.28), materials.enemyCloth);
  const legRight = legLeft.clone();

  head.position.y = 2.18;
  body.position.y = 1.46;
  armLeft.position.set(-0.58, 1.48, 0);
  armRight.position.set(0.58, 1.48, 0);
  legLeft.position.set(-0.2, 0.48, 0);
  legRight.position.set(0.2, 0.48, 0);
  group.add(head, body, armLeft, armRight, legLeft, legRight);
  group.userData.parts = { armLeft, armRight, legLeft, legRight, body, head };
  group.userData.phase = Math.random() * Math.PI * 2;
  group.userData.hitTime = 0;
  group.traverse((child) => {
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });
  return group;
}

function findNamedPart(root, name) {
  let match = null;
  root.traverse((child) => {
    if (!match && child.name.toLowerCase().includes(name)) match = child;
  });
  return match;
}

function createCharacterRig(root) {
  const parts = {
    armLeft: findNamedPart(root, "arm-left"),
    armRight: findNamedPart(root, "arm-right"),
    legLeft: findNamedPart(root, "leg-left"),
    legRight: findNamedPart(root, "leg-right"),
    torso: findNamedPart(root, "torso"),
    head: findNamedPart(root, "head"),
  };
  const base = new Map();
  for (const part of Object.values(parts)) {
    if (part) {
      base.set(part, {
        position: part.position.clone(),
        rotation: part.rotation.clone(),
      });
    }
  }
  return {
    parts,
    base,
    time: 0,
    rootY: root.position.y,
    sword: null,
    swordPivot: null,
  };
}

function attachSwordToCharacter(rig) {
  const swordPivot = new THREE.Group();
  swordPivot.position.set(0.78, 1.18, 0.24);
  swordPivot.rotation.set(-0.2, 0, -0.56);
  const sword = createSword();
  sword.position.set(0, -0.16, 0.04);
  const slashArc = new THREE.Mesh(new THREE.TorusGeometry(0.82, 0.035, 8, 32, Math.PI * 1.12), materials.slash);
  slashArc.position.set(0, 0.48, 0);
  slashArc.rotation.set(Math.PI / 2, 0, -0.65);
  slashArc.visible = false;
  swordPivot.add(sword);
  swordPivot.add(slashArc);
  player.add(swordPivot);
  rig.sword = sword;
  rig.swordPivot = swordPivot;
  rig.slashArc = slashArc;
}

function setPartPose(rig, part, target, blend) {
  if (!part) return;
  const base = rig.base.get(part);
  if (!base) return;
  part.rotation.x = THREE.MathUtils.lerp(part.rotation.x, base.rotation.x + (target.x || 0), blend);
  part.rotation.y = THREE.MathUtils.lerp(part.rotation.y, base.rotation.y + (target.y || 0), blend);
  part.rotation.z = THREE.MathUtils.lerp(part.rotation.z, base.rotation.z + (target.z || 0), blend);
}

function dampAngle(current, target, amount) {
  const delta = Math.atan2(Math.sin(target - current), Math.cos(target - current));
  return current + delta * amount;
}

function updateCharacterRig(dt, movement, sprinting) {
  if (!characterRig || !character) return;
  const { moving, backward, strafe } = movement;
  const { parts } = characterRig;
  const blend = moving ? 0.42 : 0.2;
  const strideSpeed = sprinting ? 9.8 : 7.2;
  if (moving) characterRig.time += dt * strideSpeed;
  const stride = moving ? Math.sin(characterRig.time) : 0;
  const direction = backward ? -1 : 1;
  const counterStride = -stride * direction;
  const directedStride = stride * direction;
  const strafeLean = strafe * 0.12;
  const bounce = moving ? Math.abs(Math.cos(characterRig.time)) * 0.045 : 0;

  character.position.y = THREE.MathUtils.lerp(character.position.y, characterRig.rootY + bounce, blend);
  setPartPose(characterRig, parts.armLeft, { x: directedStride * 0.46, z: -0.03 + strafeLean }, blend);
  setPartPose(characterRig, parts.armRight, { x: counterStride * 0.46, z: 0.03 + strafeLean }, blend);
  setPartPose(characterRig, parts.legLeft, { x: counterStride * 0.22, z: strafe * 0.04 }, blend);
  setPartPose(characterRig, parts.legRight, { x: directedStride * 0.22, z: strafe * 0.04 }, blend);
  setPartPose(characterRig, parts.torso, { z: stride * 0.025 - strafeLean * 0.25, x: moving ? -0.025 * direction : 0 }, blend);
  setPartPose(characterRig, parts.head, { z: stride * -0.018 + strafeLean * 0.18, x: moving ? 0.018 * direction : 0 }, blend);

  if (characterRig.swordPivot) {
    const phase = attackTime > 0 ? 1 - attackTime / ATTACK_DURATION : 0;
    const slash = Math.sin(phase * Math.PI);
    characterRig.swordPivot.rotation.x = -0.2 - slash * 0.9;
    characterRig.swordPivot.rotation.y = slash * 0.35;
    characterRig.swordPivot.rotation.z = -0.56 + slash * 1.65;
    if (characterRig.slashArc) {
      characterRig.slashArc.visible = slash > 0.08;
      characterRig.slashArc.scale.setScalar(0.78 + slash * 0.34);
      characterRig.slashArc.material.opacity = 0.18 + slash * 0.52;
    }
    setPartPose(characterRig, parts.armRight, { x: counterStride * 0.46 - slash * 0.82, y: slash * 0.28, z: 0.03 }, 0.55);
  }
}

function setupPlayer() {
  player = new THREE.Group();
  scene.add(player);
  fallbackCharacter = createFallbackHumanoid();
  player.add(fallbackCharacter);

  new GLTFLoader().load(
    CHARACTER_GLB,
    (gltf) => {
      character = gltf.scene;
      character.traverse((child) => {
        if (child.isMesh) {
          child.castShadow = true;
          child.receiveShadow = true;
          if (child.material) child.material.side = THREE.FrontSide;
        }
      });
      const box = new THREE.Box3().setFromObject(character);
      const size = new THREE.Vector3();
      const center = new THREE.Vector3();
      box.getSize(size);
      box.getCenter(center);
      character.position.sub(center);
      character.position.y += size.y * 0.5;
      character.scale.setScalar(2.4 / Math.max(size.y, 0.001));
      characterRig = createCharacterRig(character);
      attachSwordToCharacter(characterRig);
      player.remove(fallbackCharacter);
      player.add(character);
    },
    undefined,
    () => {
      character = fallbackCharacter;
      characterRig = null;
    },
  );
}

function setupEnemies() {
  enemies = [];
  for (let i = 0; i < ENEMY_COUNT; i += 1) {
    const enemy = createEnemy();
    const angle = (i / ENEMY_COUNT) * Math.PI * 2 + 0.4;
    const radius = i === 0 ? 11 : 22 + hash2(i, 17) * 24;
    enemy.position.set(Math.sin(angle) * radius, 0, Math.cos(angle) * radius);
    enemy.rotation.y = angle + Math.PI;
    scene.add(enemy);
    enemies.push(enemy);
  }
}

function updateEnemyAnimation(enemy, dt, moving) {
  const parts = enemy.userData.parts;
  enemy.userData.phase += dt * (moving ? 7.0 : 2.4);
  const stride = Math.sin(enemy.userData.phase);
  const wave = moving ? stride : stride * 0.12;
  parts.armLeft.rotation.x = wave * 0.42;
  parts.armRight.rotation.x = -wave * 0.42;
  parts.legLeft.rotation.x = -wave * 0.3;
  parts.legRight.rotation.x = wave * 0.3;
  parts.body.rotation.z = moving ? stride * 0.035 : 0;
  parts.head.rotation.z = moving ? -stride * 0.025 : 0;
  enemy.position.y = moving ? Math.abs(Math.cos(enemy.userData.phase)) * 0.035 : 0;
}

function updateEnemies(dt) {
  for (const enemy of enemies) {
    const toPlayer = new THREE.Vector3().subVectors(player.position, enemy.position);
    const distance = toPlayer.length();
    const chasing = distance < 38;
    if (chasing && distance > 2.4) {
      toPlayer.y = 0;
      toPlayer.normalize();
      enemy.position.addScaledVector(toPlayer, dt * 2.15);
      enemy.rotation.y = Math.atan2(toPlayer.x, toPlayer.z);
    } else if (!chasing) {
      const drift = Math.sin(performance.now() / 1400 + enemy.userData.phase) * 0.25;
      enemy.rotation.y += drift * dt;
    }

    if (attackTime > ATTACK_DURATION * 0.45) {
      const forward = new THREE.Vector3(Math.sin(player.rotation.y), 0, Math.cos(player.rotation.y));
      const offset = new THREE.Vector3().subVectors(enemy.position, player.position);
      const hitDistance = offset.length();
      offset.y = 0;
      const inArc = hitDistance < 3.2 && offset.normalize().dot(forward) > 0.45;
      if (inArc) {
        enemy.position.addScaledVector(forward, dt * 12);
        enemy.userData.hitTime = 0.22;
      }
    }

    if (enemy.userData.hitTime > 0) enemy.userData.hitTime -= dt;
    enemy.traverse((child) => {
      if (child.isMesh && child.material?.color) {
        child.material.emissive?.setHex(enemy.userData.hitTime > 0 ? 0x552222 : 0x000000);
      }
    });
    updateEnemyAnimation(enemy, dt, chasing);
  }
}

function triggerAttack() {
  if (!running) return;
  attackTime = ATTACK_DURATION;
}

function createTree(x, z, scale) {
  const group = new THREE.Group();
  const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.18 * scale, 0.25 * scale, 1.8 * scale, 6), materials.trunk);
  trunk.position.y = 0.9 * scale;
  const top = new THREE.Mesh(new THREE.ConeGeometry(0.95 * scale, 2.2 * scale, 7), materials.tree);
  top.position.y = 2.35 * scale;
  trunk.castShadow = true;
  top.castShadow = true;
  group.add(trunk, top);
  group.position.set(x, 0, z);
  return group;
}

function createRock(x, z, scale) {
  const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.55 * scale, 0), materials.rock);
  rock.position.set(x, 0.32 * scale, z);
  rock.rotation.set(hash2(x, z) * 2, hash2(z, x) * 2, 0);
  rock.scale.y = 0.55 + hash2(x + 8, z - 2) * 0.65;
  rock.castShadow = true;
  rock.receiveShadow = true;
  return rock;
}

function createMarker(x, z) {
  const marker = new THREE.Mesh(new THREE.OctahedronGeometry(0.45, 0), materials.marker);
  marker.position.set(x, 1.4, z);
  marker.userData.spin = true;
  marker.castShadow = true;
  return marker;
}

function createChunk(cx, cz) {
  const key = `${cx},${cz}`;
  if (chunks.has(key)) return;

  const group = new THREE.Group();
  group.userData.key = key;
  const worldX = cx * CHUNK_SIZE;
  const worldZ = cz * CHUNK_SIZE;
  const biome = biomeAt(worldX, worldZ);
  const material = biome === "Sand Flats" ? materials.sand : biome === "Pine Meadow" ? materials.dirt : materials.meadow;
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(CHUNK_SIZE, CHUNK_SIZE, 1, 1), material);
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(worldX, 0, worldZ);
  ground.receiveShadow = true;
  group.add(ground);

  for (let i = 0; i < 18; i += 1) {
    const rx = worldX - CHUNK_SIZE / 2 + hash2(cx * 31 + i, cz * 19) * CHUNK_SIZE;
    const rz = worldZ - CHUNK_SIZE / 2 + hash2(cx * 13, cz * 29 + i) * CHUNK_SIZE;
    const roll = hash2(Math.floor(rx * 10), Math.floor(rz * 10));
    const distFromOrigin = Math.hypot(rx, rz);
    if (distFromOrigin < 7) continue;
    if (biome === "Pine Meadow" && roll > 0.27) group.add(createTree(rx, rz, 0.7 + roll * 0.75));
    else if (roll > 0.82) group.add(createRock(rx, rz, 0.7 + roll * 0.8));
  }

  if (hash2(cx, cz) > 0.88 && Math.hypot(worldX, worldZ) > 20) {
    group.add(createMarker(worldX + 3, worldZ - 2));
  }

  scene.add(group);
  chunks.set(key, group);
}

function updateChunks() {
  const pcx = Math.round(player.position.x / CHUNK_SIZE);
  const pcz = Math.round(player.position.z / CHUNK_SIZE);
  const needed = new Set();
  for (let z = pcz - VIEW_RADIUS; z <= pcz + VIEW_RADIUS; z += 1) {
    for (let x = pcx - VIEW_RADIUS; x <= pcx + VIEW_RADIUS; x += 1) {
      const key = `${x},${z}`;
      needed.add(key);
      createChunk(x, z);
    }
  }
  for (const [key, group] of chunks) {
    if (!needed.has(key)) {
      scene.remove(group);
      group.traverse((child) => {
        if (child.geometry) child.geometry.dispose();
      });
      chunks.delete(key);
    }
  }
}

function updatePlayer(dt) {
  const forwardInput = Number(keys.has("KeyW") || keys.has("ArrowUp")) - Number(keys.has("KeyS") || keys.has("ArrowDown"));
  const strafeInput = Number(keys.has("KeyD") || keys.has("ArrowRight")) - Number(keys.has("KeyA") || keys.has("ArrowLeft"));
  const sprinting = keys.has("ShiftLeft") || keys.has("ShiftRight");

  camera.getWorldDirection(cameraForward);
  cameraForward.y = 0;
  cameraForward.normalize();
  cameraRight.set(cameraForward.z, 0, -cameraForward.x);
  moveVector.set(0, 0, 0);
  moveVector.addScaledVector(cameraForward, forwardInput);
  moveVector.addScaledVector(cameraRight, strafeInput);

  const moving = moveVector.lengthSq() > 0.0001;
  const backward = forwardInput < 0 && Math.abs(strafeInput) < 0.15;
  if (moving) {
    moveVector.normalize();
    const speed = PLAYER_SPEED * (sprinting ? SPRINT_MULT : 1);
    player.position.addScaledVector(moveVector, speed * dt);
    const facingVector = backward ? cameraForward : moveVector;
    const targetYaw = Math.atan2(facingVector.x, facingVector.z);
    player.rotation.y = dampAngle(player.rotation.y, targetYaw, Math.min(1, dt * 12));
  }

  const parts = fallbackCharacter?.userData?.parts;
  if (parts) {
    const direction = backward ? -1 : 1;
    const wave = moving ? Math.sin(performance.now() / 95) * 0.55 * direction : 0;
    parts.armL.rotation.x = wave;
    parts.armR.rotation.x = -wave;
    parts.legL.rotation.x = -wave * 0.55;
    parts.legR.rotation.x = wave * 0.55;
  }

  updateCharacterRig(dt, { moving, backward, strafe: strafeInput }, sprinting);
}

function updateCamera() {
  const radius = 12;
  const height = 7;
  const target = player.position.clone();
  target.y = 1.4;
  const camX = target.x + Math.sin(cameraYaw) * Math.cos(orbitPitch) * radius;
  const camZ = target.z + Math.cos(cameraYaw) * Math.cos(orbitPitch) * radius;
  const camY = target.y + height + Math.sin(orbitPitch) * 3;
  camera.position.lerp(new THREE.Vector3(camX, camY, camZ), 0.12);
  camera.lookAt(target);
}

function updateHud() {
  biomeText.textContent = biomeAt(player.position.x, player.position.z);
  positionText.textContent = `${Math.round(player.position.x)}, ${Math.round(player.position.z)}`;
}

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.04);
  if (running) {
    attackTime = Math.max(0, attackTime - dt);
    updatePlayer(dt);
    updateEnemies(dt);
    updateChunks();
    updateCamera();
    updateHud();
  }
  scene.traverse((child) => {
    if (child.userData.spin) {
      child.rotation.y += dt * 1.8;
      child.position.y = 1.4 + Math.sin(performance.now() / 420) * 0.16;
    }
  });
  renderer.render(scene, camera);
}

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function init() {
  setupRenderer();
  setupMaterials();
  setupPlayer();
  setupEnemies();
  updateChunks();
  updateCamera();
  animate();
}

window.addEventListener("resize", resize);
window.addEventListener("keydown", (event) => {
  keys.add(event.code);
  if (event.code === "Space") {
    event.preventDefault();
    triggerAttack();
  }
  if (event.code === "KeyR") {
    cameraYaw = player.rotation.y + Math.PI;
    orbitPitch = 0.74;
  }
});
window.addEventListener("keyup", (event) => keys.delete(event.code));
window.addEventListener("blur", () => keys.clear());

canvas.addEventListener("pointerdown", (event) => {
  dragging = true;
  canvas.setPointerCapture?.(event.pointerId);
});
canvas.addEventListener("pointerup", (event) => {
  dragging = false;
  canvas.releasePointerCapture?.(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!dragging) return;
  cameraYaw -= event.movementX * 0.006;
  orbitPitch = THREE.MathUtils.clamp(orbitPitch + event.movementY * 0.004, 0.15, 1.08);
});

startButton.addEventListener("click", () => {
  running = true;
  menu.classList.add("hidden");
  canvas.focus();
});

init();
