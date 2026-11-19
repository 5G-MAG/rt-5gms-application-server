-- The AS as the receiving entity of the DASH-IF push-based content ingest protocol (TS 26.512 V19.3.0 clause 8.3, DASH-IF Live Media Ingest
-- specification [3], interface 1 and interface 2) and the serving of the objects it received at an M4d service location
--
-- The configuration of an ingest endpoint is generated as JSON by openresty.py and given to the functions here; it is decoded once per endpoint.
-- The objects are files below one directory for each distribution configuration: the path of the object at M4d is the path of the file.

local pushingest = {}

local cjson = require "cjson"
local ffi = require "ffi"

pcall(ffi.cdef, [[
int mkdir(const char *path, unsigned int mode);
int rmdir(const char *path);
int link(const char *oldpath, const char *newpath);
int unlink(const char *path);
]])
local C = ffi.C

-- errno values of Linux
local EEXIST, ENOENT, ENOTDIR, EISDIR, ENOTEMPTY, ENAMETOOLONG = 17, 2, 20, 21, 39, 36

local configs = {}

function pushingest.cfg(id, json)
    local c = configs[id]
    if c == nil then
        c = cjson.decode(json)
        configs[id] = c
    end
    return c
end

-- DASH-IF Live Media Ingest, table 6: the permissible combinations of file extensions and MIME types of the objects (first of the two where it
-- lists two); the other extensions keep the type that the mime.types file of the proxy gives
local content_types = {
    m3u8 = "application/x-mpegURL", mpd = "application/dash+xml", cmfv = "video/mp4", cmfa = "audio/mp4", cmft = "application/mp4",
    cmfm = "application/mp4", mp4 = "video/mp4", m4v = "video/mp4", m4a = "audio/mp4", m4s = "video/iso.segment", init = "video/mp4",
    header = "video/mp4", key = "application/octet-stream",
}
pushingest.content_types = content_types

-- The same table 6 as the permissible MIME types of each extension (lower case): "video/mp4 or application/mp4" for .mp4, "application/x-mpegURL
-- or vnd.apple.mpegURL" for .m3u8 (the second one read as application/vnd.apple.mpegurl)
local permitted_types = {
    m3u8 = {["application/x-mpegurl"] = true, ["application/vnd.apple.mpegurl"] = true}, mpd = {["application/dash+xml"] = true},
    cmfv = {["video/mp4"] = true}, cmfa = {["audio/mp4"] = true}, cmft = {["application/mp4"] = true}, cmfm = {["application/mp4"] = true},
    mp4 = {["video/mp4"] = true, ["application/mp4"] = true}, m4v = {["video/mp4"] = true}, m4a = {["audio/mp4"] = true},
    m4s = {["video/iso.segment"] = true}, init = {["video/mp4"] = true}, header = {["video/mp4"] = true}, key = {["application/octet-stream"] = true},
}

-- Interface 2, clause 7.1.2: the combination of the extension of the object and the MIME type of the request is one of table 6. An extension that
-- the table does not list, or a request without a MIME type, is not judged here.
function pushingest.media_type_permitted(path, content_type)
    local ext = path:match("%.([^./]+)$")
    if not ext or not content_type or not permitted_types[ext:lower()] then
        return true
    end
    local media_type = content_type:match("^%s*([^;%s]+)")
    return media_type ~= nil and permitted_types[ext:lower()][media_type:lower()] == true
end

-- Interface 1, clause 6.2 (items a to c of the list on the MPEG-DASH manifest): the SegmentTemplate of each AdaptationSet has the same initialization
-- and media attributes, which contain $RepresentationID$. Returns them for a manifest that follows this, nil otherwise.
local function attribute(tag, name)
    return tag:match('%f[%w]' .. name .. '%s*=%s*"([^"]*)"') or tag:match("%f[%w]" .. name .. "%s*=%s*'([^']*)'")
end

function pushingest.templates(xml)
    local init, media
    for tag in xml:gmatch("<SegmentTemplate%s([^>]*)>") do
        local i, m = attribute(tag, "initialization"), attribute(tag, "media")
        if i and m then
            if init ~= nil and (i ~= init or m ~= media) then
                return nil
            end
            init, media = i, m
        end
    end
    if init and media and init:find("$RepresentationID$", 1, true) and media:find("$RepresentationID$", 1, true) then
        return init, media
    end
    return nil
end

local function escape(text)
    return (text:gsub("[%^%$%.%[%]%(%)%*%+%?%{%}%|\\]", "\\%0"))
end

-- The regular expression that a SegmentTemplate matches (ISO/IEC 23009-1 identifiers between $ signs); $RepresentationID$ is the capture
local function template_regex(template)
    local out, pos = {}, 1
    while pos <= #template do
        local first, last = template:find("%$[^$]*%$", pos)
        if not first then
            out[#out + 1] = escape(template:sub(pos))
            break
        end
        out[#out + 1] = escape(template:sub(pos, first - 1))
        local id = template:sub(first + 1, last - 1)
        if id == "" then
            out[#out + 1] = "\\$"
        elseif id == "RepresentationID" then
            out[#out + 1] = "(.+?)"
        elseif id:match("^Number") or id:match("^Time") or id:match("^Bandwidth") or id:match("^SubNumber") then
            out[#out + 1] = "[0-9]+"
        else
            out[#out + 1] = ".*?"
        end
        pos = last + 1
    end
    return table.concat(out)
end

local function file_exists(path)
    local fh = io.open(path, "rb")
    if not fh then
        return false
    end
    -- a directory opens, but cannot be read
    local _, err = fh:read(0)
    fh:close()
    return err == nil
end

local function reply(status)
    if status == 200 then
        ngx.status = 200
        ngx.header["Content-Length"] = "0"
        return ngx.exit(200)
    end
    return ngx.exit(status)
end

-- The path of an object relative to the ingest baseURL, as a list of its segments; nil if a segment cannot name a file ("." and "..": the
-- request left the allowed path)
local function segments(rel)
    local segs = {}
    for seg in rel:gmatch("[^/]+") do
        if seg == "." or seg == ".." then
            return nil, "outside"
        end
        if #seg > 255 or seg:find("%z") then
            return nil, "name"
        end
        segs[#segs + 1] = seg
    end
    return segs
end
pushingest.segments = segments

-- The path of an object at M4d for a distribution configuration: the path rewrite rules of the distribution (TS 26.510 V19.2.0 table 8.8.3.1-1:
-- for a push ingest the M2 upload request path is compared and the mapped path is what the distribution URL has) apply in strict order and
-- the first rule that matches replaces the part that it matches. The rules are written for the whole path of the request.
function pushingest.map_path(rules, uri, prefix)
    for _, rule in ipairs(rules) do
        local new, n = ngx.re.sub(uri, rule[1], rule[2], "jo")
        if n and n > 0 then
            if new:sub(1, #prefix) ~= prefix then
                return nil
            end
            return new:sub(#prefix + 1)
        end
    end
    return uri:sub(#prefix + 1)
end

-- Create the directories of the segments below root (mkdir -p); nil and the errno if one cannot be made
local function make_dirs(root, segs, count)
    local path = root
    for i = 1, count do
        path = path .. "/" .. segs[i]
        if C.mkdir(path, 493) ~= 0 then
            local e = ffi.errno()
            -- a directory that exists is fine; a file that is in the way shows when the object is linked into place
            if e ~= EEXIST then
                return nil, e
            end
        end
    end
    return true
end

local counter = 0
local function unique()
    counter = counter + 1
    return ngx.worker.pid() .. "-" .. math.floor(ngx.now() * 1000) .. "-" .. counter
end

-- Publish the file src as the object at the path of the segments below root. The object is a hard link to the received body, put in place with
-- rename, so that a request for it sees the old object or the new one, never half of one (DASH-IF Live Media Ingest, interface 2: an object
-- with the same path as an existing one is overwritten).
local function publish(cfg, src, root, segs)
    local ok, e = make_dirs(root, segs, #segs - 1)
    if not ok then
        return nil, e
    end
    local tmp = cfg.tmp .. "/u" .. unique()
    if C.link(src, tmp) ~= 0 then
        return nil, ffi.errno()
    end
    local renamed, _, code = os.rename(tmp, root .. "/" .. table.concat(segs, "/"))
    if not renamed then
        C.unlink(tmp)
        return nil, code
    end
    return true
end

-- A failure to store an object: what is wrong with the request (a file where a directory is needed and the reverse, a name that is too long)
-- is a 400 (DASH-IF Live Media Ingest, clause 5.3, point 5e), an error of the AS that does not relate to the request is a 5xx (point 5d)
local function failure(code)
    if code == ENOTDIR or code == EISDIR or code == ENAMETOOLONG or code == EEXIST or code == ENOTEMPTY then
        return 400
    end
    return 500
end

-- Interface 1: the templates of a manifest that was posted are kept, so that a segment of a track can be told from its initialization fragment
local function templates_key(cfg)
    return "pushtpl|" .. cfg.id
end

local function remember_manifest(cfg, segs, file, data)
    local dict = ngx.shared.m4d_ingest
    if not dict then return end
    if file then
        local fh = io.open(file, "rb")
        data = fh and fh:read(1048576)
        if fh then fh:close() end
    end
    local init, media = pushingest.templates(data or "")
    if not init then return end
    local dir = #segs > 1 and (table.concat(segs, "/", 1, #segs - 1) .. "/") or ""
    local list = cjson.decode(dict:get(templates_key(cfg)) or "{}")
    list[table.concat(segs, "/")] = {dir = dir, init = init, media = media}
    dict:set(templates_key(cfg), cjson.encode(list))
end

-- Whether the object is a media segment of a track whose initialization fragment (the object that the manifest names for the Representation)
-- is not in the store of the first distribution configuration that has one
local function init_missing(cfg, rel)
    local dict = ngx.shared.m4d_ingest
    local json = dict and dict:get(templates_key(cfg))
    if not json then return false end
    local store
    for _, d in ipairs(cfg.distributions) do
        if d.dir then store = d break end
    end
    if not store then return false end
    for _, e in pairs(cjson.decode(json)) do
        local m = ngx.re.match(rel, "^" .. escape(e.dir) .. template_regex(e.media) .. "$", "jo")
        if m and not ngx.re.find(rel, "^" .. escape(e.dir) .. template_regex(e.init) .. "$", "jo") then
            local init = e.init:gsub("%$RepresentationID%$", (m[1]:gsub("%%", "%%%%")))
            if init:find("$", 1, true) and init:gsub("%$%$", ""):find("$", 1, true) then
                return false
            end
            init = e.dir .. init:gsub("%$%$", "$")
            local mapped = pushingest.map_path(store.rules, cfg.prefix .. init, cfg.prefix)
            local segs = mapped and segments(mapped)
            return segs ~= nil and #segs > 0 and not file_exists(store.dir .. "/" .. table.concat(segs, "/"))
        end
    end
    return false
end

-- Push distribution (TS 26.510 table 8.8.3.1-1, distribution mode PUSH; TS 26.512 clauses 4.10A and 8.3): this AS is the ingest source of the
-- downstream AS. The object is sent with one HTTP POST to the distribution baseURL and the mapped path (DASH-IF clauses 5.1, 5.2 and 6.2: User-Agent,
-- one object in each request, the MIME type of table 6). It is sent after the response to the source, from a copy (a hard link) of the body.
local function push_downstream(premature, job)
    if premature then return end
    local fh = io.open(job.file, "rb")
    if not fh then
        ngx.log(ngx.ERR, "push distribution: ", job.file, " cannot be read")
        return
    end
    local size = fh:seek("end")
    fh:seek("set", 0)
    local httpc = require("resty.http").new()
    -- clause 5.3: a timeout in the order of a segment duration (1 to 6 seconds) for establishing the connection
    httpc:set_timeouts(6000, 60000, 60000)
    local res, err = httpc:request_uri(job.url, {
        method = "POST",
        body = function() return fh:read(65536) end,
        headers = {["Content-Length"] = tostring(size), ["Content-Type"] = job.ctype, ["User-Agent"] = "rt-5gms-as", ["DASH-IF-Ingest"] = "1.1"},
    })
    fh:close()
    os.remove(job.file)
    if not res then
        ngx.log(ngx.ERR, "push distribution: ", job.url, " not sent: ", err)
    elseif res.status < 200 or res.status > 299 then
        ngx.log(ngx.ERR, "push distribution: ", job.url, " answered ", res.status)
    end
end

local function push_downstream_later(cfg, src, base, msegs)
    local copy = cfg.tmp .. "/p" .. unique()
    if C.link(src, copy) ~= 0 then
        ngx.log(ngx.ERR, "push distribution: no copy of the object (errno ", ffi.errno(), ")")
        return
    end
    local escaped = {}
    for i, seg in ipairs(msegs) do escaped[i] = ngx.escape_uri(seg) end
    local url = base .. table.concat(escaped, "/")
    local ext = url:match("%.([^./?]+)$")
    local ctype = (ext and content_types[ext:lower()]) or ngx.var.content_type or "application/octet-stream"
    local ok, err = ngx.timer.at(0, push_downstream, {url = url, file = copy, ctype = ctype})
    if not ok then
        os.remove(copy)
        ngx.log(ngx.ERR, "push distribution: cannot start the transfer: ", err)
    end
end

local function handle_delete(cfg, segs, uri)
    if #segs == 0 or uri:sub(-1) == "/" then
        return reply(400)
    end
    local deleted = false
    for _, d in ipairs(cfg.distributions) do
        local mapped = d.dir and pushingest.map_path(d.rules, uri, cfg.prefix)
        local msegs = mapped and segments(mapped)
        if msegs and #msegs > 0 then
            local path = d.dir .. "/" .. table.concat(msegs, "/")
            if C.unlink(path) == 0 then
                deleted = true
                -- the folder of the object goes with its last file, unless it is the root folder; the folders above it are left
                if #msegs > 1 then
                    C.rmdir(d.dir .. "/" .. table.concat(msegs, "/", 1, #msegs - 1))
                end
            end
        end
    end
    if not deleted then
        -- the text gives no status for an object that is not there: "in all other scenarios" it is 400
        return reply(400)
    end
    return reply(200)
end

-- Receive an HTTP POST or PUT request, or a DELETE for interface 2 (clause 5.2 and 7.1.1 of the DASH-IF text) at the ingest baseURL or below it
function pushingest.handle(cfg)
    local method = ngx.req.get_method()
    local uri = ngx.var.uri
    local prefix = cfg.prefix
    if uri ~= prefix:sub(1, -2) and uri:sub(1, #prefix) ~= prefix then
        return reply(403)
    end
    local rel = uri:sub(#prefix + 1)
    local segs, why = segments(rel)
    if not segs then
        -- an object outside the allowed path, or a path that cannot be a file (clause 7.1.2, 5.3)
        return reply(why == "outside" and 403 or 400)
    end
    if method == "DELETE" and cfg.delete then
        return handle_delete(cfg, segs, uri)
    end
    if method ~= "POST" and method ~= "PUT" then
        -- clause 5.3, point 5e: in all other scenarios the receiving entity returns 400
        return reply(400)
    end
    ngx.req.read_body()
    local file = ngx.req.get_body_file()
    local data = ngx.req.get_body_data()
    if file == nil and (data == nil or #data == 0) then
        -- an empty request tests that the receiving entity is listening (clause 6.2, "or an empty request"): nothing is stored, so that it
        -- never replaces an object
        return reply(200)
    end
    if #segs == 0 or uri:sub(-1) == "/" then
        -- there is no object name to store the body under
        return reply(400)
    end
    local src = file
    if not src then
        src = cfg.tmp .. "/u" .. unique()
        local fh, err = io.open(src, "wb")
        if not fh then
            ngx.log(ngx.ERR, "push ingest: cannot write ", src, ": ", err)
            return reply(500)
        end
        fh:write(data)
        fh:close()
    end
    if cfg.check_types and not pushingest.media_type_permitted(rel, ngx.var.content_type) then
        -- clause 5.3, point 5b (a "may"): the MIME type is not one that table 6 permits for the extension
        if not file then C.unlink(src) end
        return reply(415)
    end
    if cfg.check_init and init_missing(cfg, rel) then
        -- clause 5.3, point 5c (a "may"): the object is a segment of a track whose initialization fragment has not been received
        if not file then C.unlink(src) end
        return reply(412)
    end
    local status = 200
    for _, d in ipairs(cfg.distributions) do
        local mapped = pushingest.map_path(d.rules, uri, prefix)
        local msegs = mapped and segments(mapped)
        if not msegs or #msegs == 0 then
            ngx.log(ngx.ERR, "push ingest: the path rewrite rules of the distribution ", d.dir or d.url, " do not map ", uri, " to a path")
            status = 500
            break
        end
        if d.dir then
            local ok, code = publish(cfg, src, d.dir, msegs)
            if not ok then
                ngx.log(ngx.ERR, "push ingest: cannot store ", uri, " (errno ", tostring(code), ")")
                status = failure(code)
                break
            end
        else
            push_downstream_later(cfg, src, d.url, msegs)
        end
    end
    if status == 200 and rel:match("%.mpd$") and #segs >= 1 and cfg.check_init then
        remember_manifest(cfg, segs, file, data)
    end
    if not file then
        C.unlink(src)
    end
    return reply(status)
end

-- The header fields of a response for a pushed object at M4d: its MIME type by the file extension (table 6 of the DASH-IF text) and the caching
-- directives of the first caching configuration whose urlPatternFilter matches (TS 26.512 V19.3.0 clause 7.6.4.2). The time-to-live of an object
-- counts from the time it was ingested, which is the modification time of its file.
function pushingest.response(rules)
    local ext = ngx.var.uri:match("%.([^./]+)$")
    if ext and content_types[ext:lower()] and ngx.status < 300 then
        ngx.header["Content-Type"] = content_types[ext:lower()]
    end
    local uri = ngx.var.uri
    for _, rule in ipairs(rules) do
        if ngx.re.find(uri, rule.pattern, "jo") then
            if rule.codes and not rule.codes[tostring(ngx.status)] then
                return
            end
            if rule.noCache then
                ngx.header["Cache-Control"] = "no-cache, no-store, max-age=0"
            elseif rule.maxAge then
                local age = 0
                local modified = ngx.parse_http_time(ngx.header["Last-Modified"] or "")
                if modified then
                    age = ngx.time() - modified
                end
                if age < 0 then age = 0 end
                if age > rule.maxAge then age = rule.maxAge end
                ngx.header["Cache-Control"] = "max-age=" .. (rule.maxAge - age)
            end
            return
        end
    end
end

return pushingest
