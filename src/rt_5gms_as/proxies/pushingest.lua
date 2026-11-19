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

local function handle_delete(cfg, segs, uri)
    if #segs == 0 or uri:sub(-1) == "/" then
        return reply(400)
    end
    local deleted = false
    for _, d in ipairs(cfg.distributions) do
        local mapped = pushingest.map_path(d.rules, uri, cfg.prefix)
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
    local status = 200
    for _, d in ipairs(cfg.distributions) do
        local mapped = pushingest.map_path(d.rules, uri, prefix)
        local msegs = mapped and segments(mapped)
        if not msegs or #msegs == 0 then
            ngx.log(ngx.ERR, "push ingest: the path rewrite rules of the distribution ", d.dir, " do not map ", uri, " to a path")
            status = 500
            break
        end
        local ok, code = publish(cfg, src, d.dir, msegs)
        if not ok then
            ngx.log(ngx.ERR, "push ingest: cannot store ", uri, " (errno ", tostring(code), ")")
            status = failure(code)
            break
        end
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
