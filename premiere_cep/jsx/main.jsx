// main.jsx — David Premiere CEP ExtendScript
// Loaded automatically via manifest ScriptPath.
// Called from index.html via CSInterface.evalScript("davidExecute(cmd)").

function findOrCreateBin(proj, name) {
    for (var i = 0; i < proj.rootItem.children.numItems; i++) {
        if (proj.rootItem.children[i].name === name) return proj.rootItem.children[i];
    }
    return proj.rootItem.createBin(name);
}

function addMarker(sequence, timeSeconds, label, comment) {
    var m      = sequence.markers.createMarker(timeSeconds);
    m.name     = label   || "David";
    m.comments = comment || "";
    return m;
}


function normPath(p) { return String(p).split("\\").join("/").toLowerCase(); }

function findByPath(bin, path) {
    var want = normPath(path);
    for (var i = 0; i < bin.children.numItems; i++) {
        var it = bin.children[i];
        if (it.type === ProjectItemType.BIN) {
            var inner = findByPath(it, path);
            if (inner) return inner;
        } else {
            try { if (normPath(it.getMediaPath()) === want) return it; } catch (e) {}
        }
    }
    return null;
}


// Sets Motion > Scale on a track item so a picture of w x h fills as much of the frame as fits (never stretched). Returns the percentage used or -1.
function scaleToFit(trackItem, w, h, seqW, seqH) {
    var pct = Math.round(Math.min(seqW / w, seqH / h) * 10000) / 100;
    var comps = trackItem.components;
    for (var c = 0; c < comps.numItems; c++) {
        if (comps[c].displayName === "Motion") {
            var props = comps[c].properties;
            for (var p = 0; p < props.numItems; p++) {
                if (props[p].displayName === "Scale") {
                    props[p].setValue(pct, true);
                    return pct;
                }
            }
        }
    }
    return -1;
}

function sizeOf(sizes, path) {
    var want = normPath(path);
    for (var i = 0; i < (sizes || []).length; i++) {
        if (normPath(sizes[i].path) === want) return sizes[i];
    }
    return null;
}

// Fixed action: a rough cut from the given clips (in order) on one sequence, an optional transparent title picture on video track 2 for the whole length,
// an H.264 export, and the project saved under project_path. It only ever works in an EMPTY project (it never touches one with content).
function davidRoughCut(cmd) {
    var proj = app.project;
    if (!proj) return "DAVID_ERR:No project is open. In Premiere choose File > New > Project, then run the job again.";
    if (proj.rootItem.children.numItems > 0 || proj.sequences.numSequences > 0) {
        return "DAVID_ERR:The open project (" + proj.name + ") is not empty. Close it and make a new empty project (File > New > Project), then run the job again; the job never works in a project that has content.";
    }
    var clips = cmd.clips || [];
    if (!clips.length) return "DAVID_ERR:No clips given";
    var paths = clips.slice(0);
    if (cmd.title_image) paths.push(cmd.title_image);
    var imported = proj.importFiles(paths, true, proj.rootItem, false);
    if (!imported) return "DAVID_ERR:Premiere could not import the files";
    var items = [];
    for (var k = 0; k < clips.length; k++) {
        var it = findByPath(proj.rootItem, clips[k]);
        if (!it) return "DAVID_ERR:Premiere imported the files but could not find " + clips[k];
        items.push(it);
    }
    var titleItem = null;
    if (cmd.title_image) {
        titleItem = findByPath(proj.rootItem, cmd.title_image);
        if (!titleItem) return "DAVID_ERR:Premiere imported the files but could not find the title picture";
    }
    var seq = proj.createNewSequenceFromClips(cmd.sequence_name || "David Rough Cut", items, proj.rootItem);
    if (!seq) return "DAVID_ERR:Premiere could not make the sequence";
    var v1 = seq.videoTracks[0];
    var total = 0;
    if (v1.clips.numItems > 0) total = v1.clips[v1.clips.numItems - 1].end.seconds;
    var info = {sequence: seq.name, video_tracks: seq.videoTracks.numTracks, clips_on_v1: v1.clips.numItems, seconds: total, title: "none"};
    var seqW = seq.frameSizeHorizontal, seqH = seq.frameSizeVertical, scaled = [];
    for (var m = 0; m < v1.clips.numItems; m++) {
        var tiV = v1.clips[m];
        var sz = sizeOf(cmd.sizes, tiV.projectItem.getMediaPath());
        if (sz) { var pc = scaleToFit(tiV, sz.w, sz.h, seqW, seqH); scaled.push(pc); }
    }
    info.frame = seqW + "x" + seqH;
    info.stills_scaled = scaled;
    if (titleItem && seq.videoTracks.numTracks > 1) {
        seq.videoTracks[1].overwriteClip(titleItem, 0);
        var ti = seq.videoTracks[1].clips[0];
        var tsz = sizeOf(cmd.sizes, cmd.title_image);
        if (tsz) { info.title_scale = scaleToFit(ti, tsz.w, tsz.h, seqW, seqH); }
        var e = new Time();
        e.seconds = total;
        ti.end = e;
        info.title = "placed on video track 2 for " + total + " s";
    }
    var res = seq.exportAsMediaDirect(cmd.out_path, cmd.preset_path, 0);
    info.export_result = String(res);
    proj.saveAs(cmd.project_path);
    info.project = proj.name;
    return "DAVID_OK:" + JSON.stringify(info);
}

function davidExecute(cmd) {
    try {
        if (cmd.action === "ping") {
            return "DAVID_OK:" + JSON.stringify({premiere: app.version, project: (app.project ? app.project.name : "none")});
        }
        if (cmd.action === "rough_cut") return davidRoughCut(cmd);
        var proj = app.project;
        if (!proj) return "DAVID_ERR:No project open";

        var action = cmd.action || "info";

        if (action === "info") {
            var seqCount = proj.sequences.numSequences;
            return "DAVID_OK:" + JSON.stringify({
                project:   proj.name,
                sequences: seqCount,
                active:    proj.activeSequence ? proj.activeSequence.name : "none"
            });
        }

        if (action === "create_bin") {
            var bin = findOrCreateBin(proj, cmd.bin_name || "David Assets");
            return "DAVID_OK:Bin created: " + bin.name;
        }

        if (action === "import") {
            var paths = cmd.file_paths || [];
            if (!paths.length) return "DAVID_ERR:No file_paths provided";
            proj.importFiles(paths, true, proj.rootItem, false);
            return "DAVID_OK:Imported " + paths.length + " file(s)";
        }

        if (action === "add_markers") {
            var seq = proj.activeSequence;
            if (!seq) return "DAVID_ERR:No active sequence";
            var markers = cmd.markers || [];
            for (var i = 0; i < markers.length; i++) {
                addMarker(seq, markers[i].time || 0, markers[i].label, markers[i].comment);
            }
            return "DAVID_OK:Added " + markers.length + " marker(s)";
        }

        if (action === "set_sequence_in_out") {
            var seq = proj.activeSequence;
            if (!seq) return "DAVID_ERR:No active sequence";
            if (cmd.in_point  !== undefined) seq.setInPoint(cmd.in_point);
            if (cmd.out_point !== undefined) seq.setOutPoint(cmd.out_point);
            return "DAVID_OK:In/Out points set";
        }

        if (action === "save") {
            proj.save();
            return "DAVID_OK:Project saved";
        }

        if (action === "close_project") {
            proj.save();
            proj.close();
            return "DAVID_OK:Project saved and closed";
        }

        return "DAVID_ERR:Unknown action: " + action +
               ". Valid: ping|rough_cut|info|create_bin|import|add_markers|set_sequence_in_out|save|close_project";

    } catch(e) {
        return "DAVID_ERR:" + e.message;
    }
}
