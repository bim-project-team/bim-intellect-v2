// 3D model viewer for BIM-Intellect.
//
// Renders the storey scenes exported at ingestion and highlights the elements a
// chat answer identified. One WebGL context is created for the whole app and
// re-targeted per answer: browsers cap concurrent contexts at roughly 8-16, so
// a context per message would degrade a long conversation.
//
// Loaded as a module (three.js ships as ESM) while app.js stays a classic
// script, so the API is published on window.bimViewer rather than exported.
//
// Coordinates. Scene geometry is Y-up, matching three.js, because
// bim_graph/scene_export.py emits it that way. Bounding boxes from
// /api/model/elements are already converted server-side, so nothing here needs
// to know the IFC axis convention. Extracted scenes and AABBs use the same
// canonical metre coordinate contract. Camera distances below remain expressed
// as multiples of the loaded model size so navigation scales naturally.

// Bare specifiers, resolved by the importmap in templates/index.html. Using the
// same specifier the addons use guarantees one three.js module instance; a mix
// of bare and absolute URLs would load the library twice.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// Monochrome, matching the interface: context geometry recedes, highlights read
// as the subject. Shared instances - one material per role, not per mesh, so
// nothing here needs disposing between scenes.
const CONTEXT_MATERIAL = new THREE.MeshLambertMaterial({
  color: 0xdedede,
  transparent: true,
  opacity: 0.28,
  depthWrite: false,
  side: THREE.DoubleSide,
});
const HIGHLIGHT_MATERIAL = new THREE.MeshLambertMaterial({
  color: 0x111111,
  side: THREE.DoubleSide,
});
const HIGHLIGHT_EDGE_MATERIAL = new THREE.LineBasicMaterial({ color: 0x111111 });

const FIT_MARGIN = 1.6;      // pad the framed box so it is not flush to the edge
const MIN_EXTENT = 1e-3;     // guards degenerate boxes (a single planar element)
// Bounds the parsed-glTF cache. The reference model exports 45 storey scenes
// totalling ~88 MB, with a 13 MB worst case, so an unbounded cache would grow to
// the whole building over a long conversation.
const MAX_CACHED_SCENES = 6;

export class ModelViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setClearColor(0xffffff, 1);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(55, 1, 0.05, 100000);

    // Lambert + two lights rather than unlit: the exported glTF carries no
    // NORMAL attribute, so normals are computed on load and shading is what
    // makes a box read as a wall instead of a silhouette.
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x9a9a9a, 1.7));
    const key = new THREE.DirectionalLight(0xffffff, 0.85);
    key.position.set(1, 2, 1.5);
    this.scene.add(key);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;

    this.loader = new GLTFLoader();
    this.content = new THREE.Group();
    this.scene.add(this.content);

    this.highlightKeys = new Set();
    // Bounded LRU keyed by URL: revisiting a storey during one session must not
    // re-download several MB, but keeping every visited storey resident would
    // eventually hold the whole building in GPU memory.
    this.sceneCache = new Map();
    // Monotonic token so a slow load for a superseded answer cannot overwrite
    // the scene the user is currently looking at.
    this.loadToken = 0;
    // Only geometry allocated here is tracked for disposal; cached glTF buffers
    // are shared with clones and are freed by _evictScene instead.
    this.ownedGeometry = new Set();

    this._observeSize();
    this._animate();
  }

  _observeSize() {
    // The canvas lives in a drawer that is display:none until opened, so it has
    // no size at construction. ResizeObserver both sizes it on first open and
    // keeps it correct afterwards.
    const apply = () => {
      const width = this.canvas.clientWidth;
      const height = this.canvas.clientHeight;
      if (!width || !height) return;
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    };
    new ResizeObserver(apply).observe(this.canvas);
    apply();
  }

  _animate() {
    const frame = () => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  clear() {
    for (const child of [...this.content.children]) {
      this.content.remove(child);
    }
    // Geometry allocated for the box fallback: freed here because nothing else
    // references it. Cached glTF geometry is left alone; its clones share it.
    for (const geometry of this.ownedGeometry) geometry.dispose();
    this.ownedGeometry.clear();
    this.highlightKeys.clear();
  }

  cancelAndClear() {
    // Explicit scope changes (for example deleting an IFC model) must also
    // invalidate any GLB request that was already in flight.
    this.loadToken += 1;
    this.clear();
  }

  /** Drop the least recently used parsed scene, freeing its GPU buffers. */
  _evictScene() {
    const oldest = this.sceneCache.keys().next();
    if (oldest.done) return;
    const root = this.sceneCache.get(oldest.value);
    this.sceneCache.delete(oldest.value);
    root.traverse((node) => {
      if (node.isMesh) node.geometry.dispose();
    });
  }

  /** Frame a box, keeping the current viewing direction when there is one. */
  fitTo(box) {
    if (!box || box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z, MIN_EXTENT);
    const distance = (extent * FIT_MARGIN) / Math.tan((this.camera.fov * Math.PI) / 360);

    const direction = this.camera.position.clone().sub(this.controls.target);
    if (direction.lengthSq() < MIN_EXTENT) direction.set(1, 0.75, 1);
    direction.normalize().multiplyScalar(distance);

    this.camera.position.copy(center).add(direction);
    this.camera.near = Math.max(distance / 1000, 0.01);
    this.camera.far = distance * 20;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.update();
  }

  /** Frame the union of pre-converted server-side bounding boxes. */
  fitToBounds(bounds) {
    const box = new THREE.Box3();
    for (const item of bounds || []) {
      if (!item.min || !item.max) continue;
      box.expandByPoint(new THREE.Vector3(...item.min));
      box.expandByPoint(new THREE.Vector3(...item.max));
    }
    this.fitTo(box);
  }

  async _loadScene(url) {
    if (this.sceneCache.has(url)) {
      // Re-insert so Map iteration order stays least-recently-used first.
      const cached = this.sceneCache.get(url);
      this.sceneCache.delete(url);
      this.sceneCache.set(url, cached);
      return cached;
    }
    const gltf = await this.loader.loadAsync(url);
    gltf.scene.traverse((node) => {
      if (!node.isMesh) return;
      // The exporter writes POSITION only, so shading depends on deriving
      // normals here. Skipping this renders every surface unlit.
      if (!node.geometry.getAttribute("normal")) node.geometry.computeVertexNormals();
      node.material = CONTEXT_MATERIAL;
    });
    while (this.sceneCache.size >= MAX_CACHED_SCENES) this._evictScene();
    this.sceneCache.set(url, gltf.scene);
    return gltf.scene;
  }

  /**
   * Show scenes and highlight elements.
   *
   * `highlight` entries carry ifc_guid, which is what glTF node names hold
   * (scene_export sets use-element-guids), so a highlight is a direct name
   * lookup with no separate index to keep in sync.
   *
   * `bounds` frames the camera immediately from graph data, before several MB of
   * geometry has parsed, and supplies the box fallback when no scene loads.
   */
  async show({ sceneUrls = [], highlight = [], bounds = [] } = {}) {
    const token = ++this.loadToken;
    this.highlightKeys = new Set(
      highlight.map((item) => item.ifc_guid || item.element_id).filter(Boolean),
    );

    // Load before clearing, so a slow or failing fetch does not blank the view
    // the user is currently reading.
    const loaded = [];
    for (const url of sceneUrls) {
      try {
        const root = await this._loadScene(url);
        if (token !== this.loadToken) return { loaded: 0, matched: 0, superseded: true };
        loaded.push(root);
      } catch (error) {
        // A missing scene is expected when a project was imported before scene
        // export existed; the caller falls back to boxes.
        console.warn("Scene load failed:", url, error);
      }
    }
    if (token !== this.loadToken) return { loaded: 0, matched: 0, superseded: true };

    this.clear();
    // clear() drops highlightKeys along with the scene, so restore them for the
    // material assignment below.
    this.highlightKeys = new Set(
      highlight.map((item) => item.ifc_guid || item.element_id).filter(Boolean),
    );
    if (bounds.length) this.fitToBounds(bounds);

    // Distinct elements, not meshes: one element can be several meshes.
    const matchedKeys = new Set();
    for (const root of loaded) {
      // clone(true) shares geometry with the cached original and only duplicates
      // the node hierarchy, so per-answer materials cost no extra buffers.
      const instance = root.clone(true);
      instance.traverse((node) => {
        if (!node.isMesh) return;
        const key = this._highlightKeyFor(node);
        node.material = key ? HIGHLIGHT_MATERIAL : CONTEXT_MATERIAL;
        if (key) matchedKeys.add(key);
      });
      this.content.add(instance);
    }
    const matched = matchedKeys.size;

    if (!loaded.length && bounds.length) this._addBoxes(bounds);
    if (matched || !loaded.length) this._frameHighlighted(bounds);
    return { loaded: loaded.length, matched, superseded: false };
  }

  /**
   * The highlight key this mesh belongs to, or "" if it is context geometry.
   *
   * The GUID lives on the glTF *node*, but GLTFLoader only names a Mesh after
   * that node when the mesh has exactly one primitive. Multi-primitive elements
   * (42 of 236 on one reference storey) become a Group named by GUID whose child
   * meshes are named after the IFC step id instead - so the GUID has to be looked
   * for up the ancestor chain, not just on the mesh itself.
   *
   * Returning the key rather than a boolean lets the caller count *elements*
   * highlighted, which is what the UI reports, instead of counting meshes.
   */
  _highlightKeyFor(mesh) {
    for (let node = mesh; node && node !== this.content; node = node.parent) {
      if (node.name && this.highlightKeys.has(node.name)) return node.name;
    }
    return "";
  }

  /**
   * Box fallback for projects with graph data but no exported scenes.
   * Presented as boxes precisely because that is all the graph stores - the
   * bounding volumes the clash engine itself measured.
   */
  _addBoxes(bounds) {
    for (const item of bounds) {
      if (!item.min || !item.max) continue;
      const min = new THREE.Vector3(...item.min);
      const max = new THREE.Vector3(...item.max);
      const size = max.clone().sub(min);
      const highlighted = this.highlightKeys.has(item.ifc_guid || item.element_id);
      const geometry = new THREE.BoxGeometry(
        Math.max(size.x, MIN_EXTENT), Math.max(size.y, MIN_EXTENT), Math.max(size.z, MIN_EXTENT),
      );
      this.ownedGeometry.add(geometry);
      const mesh = new THREE.Mesh(geometry, highlighted ? HIGHLIGHT_MATERIAL : CONTEXT_MATERIAL);
      mesh.position.copy(min).add(max).multiplyScalar(0.5);
      mesh.name = item.ifc_guid || item.element_id || "";
      if (highlighted) {
        const edgeGeometry = new THREE.EdgesGeometry(geometry);
        this.ownedGeometry.add(edgeGeometry);
        mesh.add(new THREE.LineSegments(edgeGeometry, HIGHLIGHT_EDGE_MATERIAL));
      }
      this.content.add(mesh);
    }
  }

  /** Zoom to the highlighted subset once it is known to be present. */
  _frameHighlighted(bounds) {
    if (!this.highlightKeys.size) return;
    const box = new THREE.Box3();
    for (const item of bounds || []) {
      if (!this.highlightKeys.has(item.ifc_guid || item.element_id)) continue;
      if (!item.min || !item.max) continue;
      box.expandByPoint(new THREE.Vector3(...item.min));
      box.expandByPoint(new THREE.Vector3(...item.max));
    }
    if (box.isEmpty()) {
      // No bounds for the highlighted elements: derive the box from the meshes
      // that actually matched instead of leaving the camera where it was.
      this.content.traverse((node) => {
        if (node.isMesh && this._highlightKeyFor(node)) box.expandByObject(node);
      });
    }
    // Highlights alone are often a thin sliver; keep some surroundings in frame.
    if (!box.isEmpty()) {
      const padding = box.getSize(new THREE.Vector3()).length() * 0.35;
      box.expandByScalar(Math.max(padding, MIN_EXTENT));
      this.fitTo(box);
    }
  }

  resetView() {
    const box = new THREE.Box3().setFromObject(this.content);
    this.fitTo(box);
  }
}

let instance = null;

window.bimViewer = {
  /** Create the single viewer on first use; the canvas must be in the DOM. */
  attach(canvas) {
    if (!instance) instance = new ModelViewer(canvas);
    return instance;
  },
  get ready() {
    return instance !== null;
  },
  show(options) {
    // Resolves with the same shape as ModelViewer.show() so callers can report
    // honestly ("nothing loaded") instead of branching on viewer availability.
    return instance
      ? instance.show(options)
      : Promise.resolve({ loaded: 0, matched: 0, superseded: false });
  },
  resetView() {
    instance?.resetView();
  },
  clear() {
    instance?.cancelAndClear();
  },
};
