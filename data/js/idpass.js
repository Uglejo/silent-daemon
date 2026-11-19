const __PALETTE404 = __PALETTE__;
const __MODE404 = __MODE__;
const __SEETHROUGH404 = __SEETHROUGH__;
function __idpass404(THREE, root) {
  const nodes = [];
  root.traverse(function (node) { if (node.isMesh || node.isLine || node.isPoints) nodes.push(node); });
  const cache = new Map();
  for (let k = 0; k < nodes.length; k++) {
    const node = nodes[k];
    const old = Array.isArray(node.material) ? node.material[0] : node.material;
    if (__SEETHROUGH404 && old && old.transparent && old.opacity < 0.75) { node.visible = false; continue; }
    if (__MODE404 === 'albedo') {
      if (node.isMesh && old) {
        node.material = new THREE.MeshBasicMaterial({ color: old.color ? old.color.clone() : 0xffffff, map: old.map || null,
          vertexColors: !!old.vertexColors, toneMapped: false, side: old.side });
      }
      continue;
    }
    let key = 0;
    if (__MODE404 === 'mesh') key = 1 + (k % (__PALETTE404.length - 1));
    else key = node.geometry && node.geometry.userData.site ? node.geometry.userData.site : 0;
    key = Math.max(0, Math.min(__PALETTE404.length - 1, key));
    const kind = node.isMesh ? 'm' : node.isPoints ? 'p' : 'l';
    const id = kind + key;
    if (!cache.has(id)) {
      const color = __PALETTE404[key];
      cache.set(id, node.isMesh ? new THREE.MeshBasicMaterial({ color: color, toneMapped: false, side: THREE.DoubleSide })
        : node.isPoints ? new THREE.PointsMaterial({ color: color, toneMapped: false, size: node.material && node.material.size ? node.material.size : 1 })
        : new THREE.LineBasicMaterial({ color: color, toneMapped: false }));
    }
    node.material = cache.get(id);
    if (node.isInstancedMesh) node.instanceColor = null;
  }
}
