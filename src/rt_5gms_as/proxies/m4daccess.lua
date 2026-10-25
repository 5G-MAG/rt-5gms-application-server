-- Access control and response rules of an M4d service location (TS 26.512 V19.3.0 clauses 7.6.4.2, 7.6.4.5 and 7.6.4.6)
--
-- The configuration of a location is generated as JSON by openresty.py and given to the functions here; it is decoded once per location.

local m4daccess = {}

local cjson = require "cjson"
local ffi = require "ffi"
local resty_sha512 = require "resty.sha512"
local resty_string = require "resty.string"

ffi.cdef[[
int inet_pton(int af, const char *src, void *dst);
]]

local configs = {}

function m4daccess.cfg(id, json)
    local c = configs[id]
    if c == nil then
        c = cjson.decode(json)
        -- JSON null is not nil
        for k, v in pairs(c) do
            if v == cjson.null then c[k] = nil end
        end
        configs[id] = c
    end
    return c
end

-- base64url (RFC 4648 section 5) without padding
local function base64url(bin)
    local s = ngx.encode_base64(bin)
    s = s:gsub("%+", "-"):gsub("/", "_"):gsub("=", "")
    return s
end

-- Clause 7.6.4.5: the query presented with a request for a resource that matches urlSignature.urlPattern is validated; every failure is a 403
function m4daccess.verify_signature(sig)
    local url = ngx.var.scheme .. "://" .. ngx.var.http_host .. (ngx.var.request_uri:match("^[^?#]*"))
    if not ngx.re.find(url, sig.urlPattern, "jo") then
        return true
    end
    local args = ngx.req.get_uri_args()
    local token = args[sig.tokenName]
    local expiry = args[sig.tokenExpiryName]
    -- step 1: the token is absent or malformed
    if type(token) ~= "string" or not ngx.re.find(token, "^[A-Za-z0-9_-]+$", "jo") then
        return false
    end
    -- step 2: the expiry is absent, malformed or has passed
    if type(expiry) ~= "string" or not ngx.re.find(expiry, "^[0-9]+$", "jo") or tonumber(expiry) < ngx.time() then
        return false
    end
    -- step 3: the token is computed again
    local signed = url .. "&" .. sig.tokenExpiryName .. "=" .. expiry
    if sig.useIPAddress then
        signed = signed .. "&" .. sig.ipAddressName .. "=" .. ngx.var.remote_addr
    end
    signed = signed .. "&" .. sig.passphraseName .. "=" .. sig.passphrase
    local sha = resty_sha512:new()
    sha:update(signed)
    local expected = base64url(sha:final())
    if #expected ~= #token then
        return false
    end
    local diff = 0
    for i = 1, #expected do
        diff = bit.bor(diff, bit.bxor(expected:byte(i), token:byte(i)))
    end
    return diff == 0
end

-- Clause 7.6.4.6: access is limited to the UEs located in the zones, which the operator maps from the addresses of the UEs (a file of
-- address ranges with the country of each, for the locator type iso3166)
local ranges = nil

local function parse_address(text)
    local buf = ffi.new("uint8_t[16]")
    if ffi.C.inet_pton(2, text, buf) == 1 then
        -- IPv4 as an IPv4-mapped IPv6 address
        local mapped = ffi.new("uint8_t[16]")
        mapped[10] = 0xff
        mapped[11] = 0xff
        for i = 0, 3 do mapped[12 + i] = buf[i] end
        return mapped
    end
    if ffi.C.inet_pton(10, text, buf) == 1 then
        return buf
    end
    return nil
end

local function load_ranges(path)
    local list = {}
    local f = io.open(path, "r")
    if not f then
        return list
    end
    for line in f:lines() do
        local cidr, country = line:match("^%s*([%x%.:]+/%d+)%s*[, ]%s*(%a%a)%s*$")
        if cidr then
            local addr, bits = cidr:match("^(.*)/(%d+)$")
            local bin = parse_address(addr)
            if bin then
                bits = tonumber(bits)
                if not addr:find(":", 1, true) then bits = bits + 96 end
                list[#list + 1] = {bin = bin, bits = bits, country = country:upper()}
            end
        end
    end
    f:close()
    return list
end

local function in_range(bin, range)
    local full = math.floor(range.bits / 8)
    for i = 0, full - 1 do
        if bin[i] ~= range.bin[i] then return false end
    end
    local rest = range.bits % 8
    if rest > 0 then
        local mask = bit.band(bit.lshift(0xff, 8 - rest), 0xff)
        if bit.band(bin[full], mask) ~= bit.band(range.bin[full], mask) then return false end
    end
    return true
end

function m4daccess.country_of(address, path)
    if ranges == nil then
        ranges = load_ranges(path)
    end
    local bin = parse_address(address)
    if not bin then return nil end
    local best, best_bits = nil, -1
    for _, r in ipairs(ranges) do
        if r.bits > best_bits and in_range(bin, r) then
            best, best_bits = r.country, r.bits
        end
    end
    return best
end

function m4daccess.check_geo(geo, ranges_file)
    local country = m4daccess.country_of(ngx.var.remote_addr, ranges_file)
    if not country then
        return false
    end
    for _, loc in ipairs(geo.locators) do
        -- an ISO 3166-1 alpha-2 country code, or an ISO 3166-2 code that begins with it
        local l = loc:upper()
        if l == country or l:sub(1, 3) == country .. "-" then
            return true
        end
    end
    return false
end

-- CORS response headers for the domain name alias of a distribution configuration (TS 26.510 table 8.8.3.1-1, domainNameAlias)
function m4daccess.cors(aliases)
    local origin = ngx.var.http_origin
    if not origin then return end
    local host = origin:match("^https?://([^/:]+)")
    if not host then return end
    for _, alias in ipairs(aliases) do
        if host:lower() == alias:lower() then
            ngx.header["Access-Control-Allow-Origin"] = origin
            ngx.header["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            ngx.header["Access-Control-Allow-Headers"] = "Range, Origin, Accept, Content-Type, CMCD-Request, CMCD-Object, CMCD-Status, CMCD-Session"
            ngx.header["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Date"
            ngx.header["Vary"] = "Origin"
            return
        end
    end
end

-- Clause 7.6.4.2: of the header fields of the origin response only those that describe the representation, the caching directives and
-- the connection are passed on; the other origin fields are not forwarded
local keep = {
    ["content-type"] = true, ["content-length"] = true, ["content-range"] = true, ["content-encoding"] = true,
    ["content-language"] = true, ["content-disposition"] = true, ["accept-ranges"] = true, ["transfer-encoding"] = true,
    ["connection"] = true, ["keep-alive"] = true, ["date"] = true, ["server"] = true, ["age"] = true, ["vary"] = true,
    ["cache-control"] = true, ["expires"] = true, ["etag"] = true, ["last-modified"] = true, ["location"] = true,
}

function m4daccess.filter_origin_headers()
    local h = ngx.resp.get_headers(0, true)
    for name, _ in pairs(h) do
        local lname = name:lower()
        if not keep[lname] and lname:sub(1, 15) ~= "access-control-" then
            ngx.header[name] = nil
        end
    end
end

return m4daccess
